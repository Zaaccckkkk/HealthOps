import Foundation

/// One page of samples from the device store plus the anchor that follows it.
public struct SamplePage: Sendable {
    /// Samples the reader could represent (for example only asleep sleep states).
    public var samples: [RawSample]
    /// Everything the device query counted against its limit: returned samples (before the
    /// reader dropped any) plus deleted objects. Paging ends only when this is smaller than
    /// the requested limit.
    public var queryReturnedCount: Int
    public var deletedCount: Int
    public var anchor: Data?
    public init(samples: [RawSample], queryReturnedCount: Int, deletedCount: Int, anchor: Data?) {
        self.samples = samples
        self.queryReturnedCount = queryReturnedCount
        self.deletedCount = deletedCount
        self.anchor = anchor
    }
}

public protocol SampleSource: Sendable {
    /// Returns at most `limit` samples added after `anchor`, with the next anchor.
    func fetchPage(after anchor: Data?, limit: Int) async throws -> SamplePage
}

public enum UploadError: Error, Equatable {
    case unauthorized
    case conflict
    case invalid(String)
    case server(Int)
    case network(String)
}

/// Raised when the owner cancelled the sync (consent revoked, identity changed, app request).
public struct SyncCancelled: Error, Equatable {
    public init() {}
}

public protocol Uploader: Sendable {
    func upload(body: Data) async throws -> BatchResult
}

public struct SyncReport: Equatable, Sendable {
    public var pagesCommitted = 0
    public var inserted = 0
    public var duplicates = 0
    public var uploadsSent = 0
    public var skippedByConsent = 0
    public var skippedBeforeHistoryStart = 0
    public var mappingFailures = 0
    public var pendingRetained = false
    public var cancelled = false
    public var error: String?
    public init() {}
}

/// Reads a page, persists it as pending together with its candidate anchor, uploads it, and
/// only then commits the anchor. Any failure keeps the pending page for an exact replay.
///
/// The engine is constructed from a frozen context (scope, uploader, consent); callers that
/// revoke consent or change identity cancel it through `isCancelled`. Cancellation is checked
/// before every device read and before every upload; a request already in flight cannot be
/// recalled, and its page stays pending until the owner decides what to do with it.
public final class SyncEngine: @unchecked Sendable {
    public let scope: SyncScope
    private let source: SampleSource
    private let uploader: Uploader
    private let store: SyncStore
    private let enabledMetrics: Set<Metric>
    private let pageSize: Int
    private let mapper = Mapper()
    private let now: @Sendable () -> Date
    private let isCancelled: @Sendable () -> Bool

    public init(
        scope: SyncScope, source: SampleSource, uploader: Uploader, store: SyncStore,
        enabledMetrics: Set<Metric>, pageSize: Int = 200,
        now: @escaping @Sendable () -> Date = { Date() },
        isCancelled: @escaping @Sendable () -> Bool = { Task.isCancelled }
    ) {
        precondition((1...500).contains(pageSize), "a page is one request; the server accepts 1-500")
        self.scope = scope
        self.source = source
        self.uploader = uploader
        self.store = store
        self.enabledMetrics = enabledMetrics
        self.pageSize = pageSize
        self.now = now
        self.isCancelled = isCancelled
    }

    public func sync(maxPages: Int = Int.max) async -> SyncReport {
        var report = SyncReport()
        do {
            var state = try store.load(scope)
            if state.firstSyncStartedAt == nil {
                state.firstSyncStartedAt = now()
                try store.save(state, for: scope)
            }
            var pages = 0
            while pages < maxPages {
                if isCancelled() { throw SyncCancelled() }
                if state.pending == nil {
                    let page = try await source.fetchPage(after: state.committedAnchor, limit: pageSize)
                    let encoded = try encode(page.samples)
                    report.skippedByConsent += encoded.skippedByConsent
                    report.skippedBeforeHistoryStart += encoded.skippedBeforeHistoryStart
                    report.mappingFailures += encoded.unmappable.count
                    if encoded.kept == 0 {
                        // Nothing to upload: never send an empty batch. Still record deletions and
                        // unmappable samples, and advance the anchor when the query moved it.
                        if page.anchor != state.committedAnchor || page.deletedCount > 0
                            || !encoded.unmappable.isEmpty {
                            state.committedAnchor = page.anchor
                            state.unsyncedDeletedCount += page.deletedCount
                            state.unmappable += encoded.unmappable
                            try store.save(state, for: scope)
                        }
                        // The query, not the filtered result, decides whether more pages exist.
                        if page.queryReturnedCount < pageSize { break }
                        pages += 1
                        continue
                    }
                    state.pending = PendingPage(
                        payload: encoded.body, candidateAnchor: page.anchor, sampleCount: encoded.kept,
                        queryReturnedCount: page.queryReturnedCount, deletedCount: page.deletedCount,
                        unmappable: encoded.unmappable, createdAt: now())
                    try store.save(state, for: scope)
                }
                guard let pending = state.pending else { break }
                if isCancelled() { throw SyncCancelled() }
                report.uploadsSent += 1
                let result = try await uploader.upload(body: pending.payload)
                state.committedAnchor = pending.candidateAnchor
                state.unsyncedDeletedCount += pending.deletedCount
                state.unmappable += pending.unmappable
                state.totalInserted += result.inserted
                state.totalDuplicates += result.duplicates
                state.lastCommittedAt = now()
                state.pending = nil
                try store.save(state, for: scope)
                report.pagesCommitted += 1
                report.inserted += result.inserted
                report.duplicates += result.duplicates
                pages += 1
                if pending.queryReturnedCount < pageSize { break }
            }
        } catch {
            report.error = Self.describe(error)
            report.cancelled = error is SyncCancelled
            report.pendingRetained = (try? store.load(scope).pending) != nil
        }
        return report
    }

    struct EncodedPage {
        var body: Data
        var kept: Int
        var skippedByConsent: Int
        var skippedBeforeHistoryStart: Int
        var unmappable: [UnmappableSample]
    }

    /// Deterministic body for a page: history bound, consent filter, mapping, stable encoding.
    func encode(_ samples: [RawSample]) throws -> EncodedPage {
        var observations: [CanonicalObservation] = []
        var skippedByConsent = 0
        var skippedBeforeHistoryStart = 0
        var unmappable: [UnmappableSample] = []
        for sample in samples {
            // Defensive copy of the device predicate: nothing that starts before the consented
            // history start is uploaded, even if a reader returned it.
            guard sample.start >= scope.historyStart else {
                skippedBeforeHistoryStart += 1
                continue
            }
            guard enabledMetrics.contains(sample.kind.metric) else {
                skippedByConsent += 1
                continue
            }
            do {
                observations.append(try mapper.map(sample))
            } catch {
                unmappable.append(UnmappableSample(
                    uuid: sample.uuid, kind: sample.kind.rawValue, start: sample.start,
                    reason: String(describing: error)))
            }
        }
        observations.sort { ($0.startedAt, $0.sourceRecordId) < ($1.startedAt, $1.sourceRecordId) }
        let body = try ContractEncoding.encoder().encode(ObservationBatch(observations: observations))
        return EncodedPage(
            body: body, kept: observations.count, skippedByConsent: skippedByConsent,
            skippedBeforeHistoryStart: skippedBeforeHistoryStart, unmappable: unmappable)
    }

    public static func describe(_ error: Error) -> String {
        switch error {
        case is SyncCancelled:
            return "cancelled before the next request; any pending page is kept"
        case UploadError.unauthorized: return "unauthorized: the development token was rejected"
        case UploadError.conflict:
            return "conflict: a source record already exists with different content; stopped"
        case let UploadError.invalid(detail): return "invalid request: \(detail)"
        case let UploadError.server(status): return "server error \(status); the page will be retried"
        case let UploadError.network(detail): return "network: \(detail); the page will be retried"
        default: return String(describing: error)
        }
    }
}
