import Foundation
import XCTest
@testable import HealthOpsCore

/// In-memory HealthKit stand-in. Like HKAnchoredObjectQuery, a page holds up to `limit`
/// events, each either a sample, a sample the reader drops (for example in-bed sleep), or a
/// deleted object. The anchor is the event index.
final class FakeSource: SampleSource, @unchecked Sendable {
    enum Event { case sample(RawSample), dropped, deleted }
    var events: [Event] = []
    var fetches = 0

    /// Convenience for tests that only need samples (`nil` = dropped by the reader).
    var samples: [RawSample?] {
        get { events.compactMap { if case let .sample(s) = $0 { return s } else { return nil } } }
        set { events = newValue.map { $0.map(Event.sample) ?? .dropped } }
    }

    func fetchPage(after anchor: Data?, limit: Int) async throws -> SamplePage {
        fetches += 1
        let start = anchor.map { Int(String(decoding: $0, as: UTF8.self))! } ?? 0
        let end = min(start + limit, events.count)
        let page = Array(events[start..<end])
        let next = page.isEmpty ? anchor : Data(String(end).utf8)
        var samples: [RawSample] = []
        var deleted = 0
        for event in page {
            switch event {
            case let .sample(s): samples.append(s)
            case .dropped: break
            case .deleted: deleted += 1
            }
        }
        return SamplePage(samples: samples, queryReturnedCount: page.count, deletedCount: deleted, anchor: next)
    }
}

/// Fake server with the real replay semantics: inserted once per source id, duplicates after.
final class FakeUploader: Uploader, @unchecked Sendable {
    var seen: Set<String> = []
    var bodies: [Data] = []
    var failNext: UploadError?
    /// Simulates "server saved but the client never got the response".
    var dropResponseNext = false

    func upload(body: Data) async throws -> BatchResult {
        bodies.append(body)
        if let error = failNext {
            failNext = nil
            throw error
        }
        let batch = try ContractEncoding.decoder().decode(ObservationBatch.self, from: body)
        var inserted = 0
        for observation in batch.observations where !seen.contains(observation.sourceRecordId) {
            seen.insert(observation.sourceRecordId)
            inserted += 1
        }
        if dropResponseNext {
            dropResponseNext = false
            throw UploadError.network("timeout")
        }
        return BatchResult(received: batch.observations.count, inserted: inserted,
                           duplicates: batch.observations.count - inserted)
    }
}

final class MemoryStore: SyncStore, @unchecked Sendable {
    var states: [String: SyncState] = [:]
    func load(_ scope: SyncScope) throws -> SyncState { states[scope.key] ?? SyncState() }
    func save(_ state: SyncState, for scope: SyncScope) throws {
        var state = state
        state.scope = scope
        states[scope.key] = state
    }
    func allStates() throws -> [SyncState] { Array(states.values) }
}

let historyStart = isoDate("2026-09-01T00:00:00Z")

final class SyncEngineTests: XCTestCase {
    let scope = SyncScope(serverURL: URL(string: "http://127.0.0.1:8000")!, userID: "user-a",
                          typeIdentifier: "HKQuantityTypeIdentifierHeartRate", historyStart: historyStart)
    var source = FakeSource()
    var uploader = FakeUploader()
    var store = MemoryStore()

    override func setUp() {
        source = FakeSource()
        uploader = FakeUploader()
        store = MemoryStore()
        source.samples = (0..<5).map { i in
            RawSample(kind: .heartRate, uuid: UUID(uuidString: String(format: "00000000-0000-4000-8000-%012d", i))!,
                      value: Double(60 + i), start: isoDate("2026-10-01T12:0\(i):00Z"),
                      end: isoDate("2026-10-01T12:0\(i):00Z"), sourceName: "Synthetic Source")
        }
    }

    func engine(pageSize: Int = 2, metrics: Set<Metric> = [.heartRate],
                isCancelled: @escaping @Sendable () -> Bool = { false }) -> SyncEngine {
        SyncEngine(scope: scope, source: source, uploader: uploader, store: store,
                   enabledMetrics: metrics, pageSize: pageSize, now: { isoDate("2026-10-02T00:00:00Z") },
                   isCancelled: isCancelled)
    }

    func sampleAt(_ i: Int, kind: RawSample.Kind = .heartRate, start: String? = nil, zone: String? = nil) -> RawSample {
        let when = isoDate(start ?? "2026-10-01T12:0\(i):00Z")
        return RawSample(kind: kind, uuid: UUID(uuidString: String(format: "00000000-0000-4000-8000-%012d", 100 + i))!,
                         value: 70, start: when, end: when, sourceName: "Synthetic Source", timeZoneIdentifier: zone)
    }

    func testFirstSyncInsertsEverythingPageByPage() async throws {
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 5)
        XCTAssertEqual(report.pagesCommitted, 3)  // 2 + 2 + 1
        XCTAssertEqual(uploader.bodies.count, 3)
        let state = try store.load(scope)
        XCTAssertEqual(state.committedAnchor, Data("5".utf8))
        XCTAssertNil(state.pending)
        XCTAssertEqual(state.totalInserted, 5)
    }

    func testIncrementalSyncWithoutNewSamplesSendsNothing() async throws {
        _ = await engine().sync()
        let before = uploader.bodies.count
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.uploadsSent, 0)
        XCTAssertEqual(uploader.bodies.count, before)
    }

    func testReplayingTheSamePayloadYieldsDuplicates() async throws {
        _ = await engine().sync()
        let body = uploader.bodies[0]
        let result = try await uploader.upload(body: body)
        XCTAssertEqual(result.inserted, 0)
        XCTAssertEqual(result.duplicates, 2)
    }

    func testFailureKeepsPendingPageAndAnchorDoesNotAdvance() async throws {
        uploader.failNext = .network("offline")
        let first = await engine().sync()
        XCTAssertNotNil(first.error)
        XCTAssertTrue(first.pendingRetained)
        var state = try store.load(scope)
        XCTAssertNil(state.committedAnchor)
        XCTAssertEqual(state.pending?.sampleCount, 2)
        let pendingBody = state.pending!.payload

        let second = await engine().sync()  // back online: exact replay first, then continue
        XCTAssertNil(second.error)
        XCTAssertEqual(uploader.bodies[1], pendingBody)  // byte-identical replay
        XCTAssertEqual(second.inserted, 5)
        state = try store.load(scope)
        XCTAssertEqual(state.committedAnchor, Data("5".utf8))
        XCTAssertNil(state.pending)
        XCTAssertEqual(uploader.seen.count, 5)
    }

    func testServerSavedButResponseLostDoesNotDuplicate() async throws {
        uploader.dropResponseNext = true
        _ = await engine().sync()
        XCTAssertEqual(uploader.seen.count, 2)  // first page stored server-side
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.duplicates, 2)  // the replayed page is recognised, not re-inserted
        XCTAssertEqual(report.inserted, 3)
        XCTAssertEqual(uploader.seen.count, 5)
    }

    func testConflictStopsWithoutAdvancingAnchor() async throws {
        uploader.failNext = .conflict
        let report = await engine().sync()
        XCTAssertTrue(report.error?.contains("conflict") ?? false)
        XCTAssertNil(try store.load(scope).committedAnchor)
        XCTAssertTrue(report.pendingRetained)
    }

    func testConsentFilterSkipsMetricsAndNeverSendsEmptyBatches() async throws {
        let report = await engine(metrics: [.steps]).sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.uploadsSent, 0)
        XCTAssertEqual(report.skippedByConsent, 5)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("5".utf8))  // anchor still advances
    }

    func testDeletionsAreCountedPersistentlyNotSynced() async throws {
        source.events = [.sample(sampleAt(0)), .deleted, .sample(sampleAt(2)), .deleted, .deleted]
        _ = await engine().sync()
        XCTAssertEqual(try store.load(scope).unsyncedDeletedCount, 3)
        _ = await engine().sync()
        XCTAssertEqual(try store.load(scope).unsyncedDeletedCount, 3)  // survives, does not reset
    }

    func testPageOfOnlyDeletionsDoesNotEndTheSync() async throws {
        // Page 1 (limit 2) is two deleted objects; later pages hold new samples.
        source.events = [.deleted, .deleted, .sample(sampleAt(2)), .sample(sampleAt(3)), .sample(sampleAt(4))]
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 3)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("5".utf8))
        XCTAssertEqual(try store.load(scope).unsyncedDeletedCount, 2)
    }

    func testMixedSampleAndDeletionPageCountsAsFull() async throws {
        // Page 1: one sample + one deletion = a full page of 2; page 2 still has a sample.
        source.events = [.sample(sampleAt(0)), .deleted, .sample(sampleAt(2))]
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 2)
        XCTAssertEqual(report.uploadsSent, 2)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("3".utf8))
    }

    func testPendingReplayContinuesPagingWhenItsPageWasFull() async throws {
        // The pending page has 1 sample + 1 deletion (full); after replay the engine must read on.
        source.events = [.sample(sampleAt(0)), .deleted, .sample(sampleAt(2))]
        uploader.failNext = .network("offline")
        _ = await engine().sync()
        XCTAssertEqual(try store.load(scope).pending?.queryReturnedCount, 2)
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 2)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("3".utf8))
    }

    func testScopesIsolateUsersAndServers() throws {
        let other = SyncScope(serverURL: scope.serverURL, userID: "user-b", typeIdentifier: scope.typeIdentifier,
                              historyStart: historyStart)
        XCTAssertNotEqual(scope.key, other.key)
        let narrower = SyncScope(serverURL: scope.serverURL, userID: "user-a", typeIdentifier: scope.typeIdentifier,
                                 historyStart: isoDate("2026-10-01T00:00:00Z"))
        XCTAssertNotEqual(scope.key, narrower.key)  // the query configuration is part of the scope
        var state = SyncState()
        state.committedAnchor = Data("5".utf8)
        try store.save(state, for: scope)
        XCTAssertNil(try store.load(other).committedAnchor)
        let consent = ConsentRecord(serverURL: scope.serverURL, userID: "user-a", enabledMetrics: [.heartRate],
                                    historyStart: isoDate("2026-09-01T00:00:00Z"), grantedAt: isoDate("2026-10-01T00:00:00Z"))
        XCTAssertTrue(consent.covers(serverURL: scope.serverURL, userID: "user-a"))
        XCTAssertFalse(consent.covers(serverURL: scope.serverURL, userID: "user-b"))
        XCTAssertFalse(consent.covers(serverURL: URL(string: "http://10.0.0.5:8000")!, userID: "user-a"))
    }

    func testFileStoreRoundTripsStateAndConsent() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let fileStore = try FileSyncStore(directory: dir)
        var state = SyncState()
        state.pending = PendingPage(payload: Data("{}".utf8), candidateAnchor: Data("3".utf8), sampleCount: 2,
                                    queryReturnedCount: 2, deletedCount: 1, unmappable: [],
                                    createdAt: isoDate("2026-10-01T00:00:00Z"))
        try fileStore.save(state, for: scope)
        var expected = state
        expected.scope = scope
        XCTAssertEqual(try fileStore.load(scope), expected)
        XCTAssertEqual(try fileStore.allStates().count, 1)
        let consentStore = try FileConsentStore(file: dir.appendingPathComponent("consent.json"))
        let consent = ConsentRecord(serverURL: scope.serverURL, userID: "user-a", enabledMetrics: [.steps, .heartRate],
                                    historyStart: isoDate("2026-09-01T00:00:00Z"), grantedAt: isoDate("2026-10-01T00:00:00Z"))
        try consentStore.save(consent)
        XCTAssertEqual(try consentStore.load(), consent)
        try consentStore.save(nil)
        XCTAssertNil(try consentStore.load())
    }

    // MARK: Review fixes

    func testCancellationStopsBeforeTheNextRequestAndKeepsPending() async throws {
        final class Flag: @unchecked Sendable { var cancelled = false }
        let flag = Flag()
        let cancellingUploader = uploader
        let engine = SyncEngine(
            scope: scope, source: source, uploader: cancellingUploader, store: store,
            enabledMetrics: [.heartRate], pageSize: 2, now: { isoDate("2026-10-02T00:00:00Z") },
            isCancelled: { flag.cancelled })
        // Cancel right after the first page is confirmed (as a consent revocation would).
        uploader.failNext = nil
        let first = await engine.sync(maxPages: 1)
        XCTAssertEqual(first.pagesCommitted, 1)
        flag.cancelled = true
        let second = await engine.sync()
        XCTAssertTrue(second.cancelled)
        XCTAssertEqual(second.uploadsSent, 0)
        XCTAssertEqual(uploader.bodies.count, 1)  // no request after cancellation
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("2".utf8))
    }

    func testCancellationBetweenReadAndUploadKeepsThePendingPage() async throws {
        final class Flag: @unchecked Sendable { var cancelled = false }
        let flag = Flag()
        // The source flips the flag while delivering the page: consent revoked mid-read.
        final class CancellingSource: SampleSource, @unchecked Sendable {
            let inner: FakeSource; let flag: Flag
            init(_ inner: FakeSource, _ flag: Flag) { self.inner = inner; self.flag = flag }
            func fetchPage(after anchor: Data?, limit: Int) async throws -> SamplePage {
                let page = try await inner.fetchPage(after: anchor, limit: limit)
                flag.cancelled = true
                return page
            }
        }
        let engine = SyncEngine(
            scope: scope, source: CancellingSource(source, flag), uploader: uploader, store: store,
            enabledMetrics: [.heartRate], pageSize: 2, isCancelled: { flag.cancelled })
        let report = await engine.sync()
        XCTAssertTrue(report.cancelled && report.pendingRetained)
        XCTAssertEqual(uploader.bodies.count, 0)
        XCTAssertNil(try store.load(scope).committedAnchor)
    }

    func testPagingUsesQueryReturnedCountNotFilteredCount() async throws {
        // Page 1: two returned, one dropped by the reader. Page 2: two more. Page 3: one.
        source.samples = [sampleAt(0), nil, sampleAt(2), sampleAt(3), sampleAt(4)]
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 4)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("5".utf8))
    }

    func testFullyFilteredFirstPageDoesNotEndTheSync() async throws {
        source.samples = [nil, nil, sampleAt(2), sampleAt(3)]
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.inserted, 2)
        XCTAssertEqual(report.uploadsSent, 1)
        XCTAssertEqual(try store.load(scope).committedAnchor, Data("4".utf8))
    }

    func testUnmappableSamplesArePersistedAndReportedNotDropped() async throws {
        source.samples = [sampleAt(0), sampleAt(1, zone: "Mars/Olympus"), sampleAt(2)]
        let report = await engine().sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.mappingFailures, 1)
        XCTAssertEqual(report.inserted, 2)
        let state = try store.load(scope)
        XCTAssertEqual(state.unmappable.count, 1)
        XCTAssertEqual(state.unmappable[0].uuid, sampleAt(1).uuid)
        XCTAssertTrue(state.unmappable[0].reason.contains("invalidTimeZone"))
        XCTAssertEqual(state.committedAnchor, Data("3".utf8))
        // A later sync does not lose the record.
        _ = await engine().sync()
        XCTAssertEqual(try store.load(scope).unmappable.count, 1)
    }

    func testSamplesBeforeHistoryStartAreNeverUploaded() async throws {
        source.samples = [
            sampleAt(0, start: "2026-08-31T23:59:59Z"),  // before the consented start
            sampleAt(1, start: "2026-09-01T00:00:00Z"),  // exactly at the start: included
            sampleAt(2),
        ]
        let report = await engine(pageSize: 3).sync()
        XCTAssertNil(report.error)
        XCTAssertEqual(report.skippedBeforeHistoryStart, 1)
        XCTAssertEqual(report.inserted, 2)
        XCTAssertFalse(uploader.seen.contains(sampleAt(0).uuid.uuidString.lowercased()))
    }

    func testReconsentWithNarrowerHistoryDiscardsOldPendingPage() async throws {
        uploader.failNext = .network("offline")
        _ = await engine().sync()
        XCTAssertNotNil(try store.load(scope).pending)
        let narrower = SyncScope(serverURL: scope.serverURL, userID: scope.userID,
                                 typeIdentifier: scope.typeIdentifier, historyStart: isoDate("2026-10-01T00:00:00Z"))
        let discarded = try SyncMaintenance.discardPendingOutsideConsent(
            store: store, serverURL: scope.serverURL, userID: scope.userID, historyStart: narrower.historyStart)
        XCTAssertEqual(discarded, 1)
        XCTAssertNil(try store.load(scope).pending)
        // The new scope starts from a fresh anchor; nothing from the old queue is replayed.
        XCTAssertNil(try store.load(narrower).committedAnchor)
        // Another user's pending page is untouched.
        let otherUser = SyncScope(serverURL: scope.serverURL, userID: "user-b",
                                  typeIdentifier: scope.typeIdentifier, historyStart: historyStart)
        var other = SyncState()
        other.pending = PendingPage(payload: Data("{}".utf8), candidateAnchor: nil, sampleCount: 1,
                                    queryReturnedCount: 1, deletedCount: 0, unmappable: [],
                                    createdAt: isoDate("2026-10-01T00:00:00Z"))
        try store.save(other, for: otherUser)
        XCTAssertEqual(try SyncMaintenance.discardPendingOutsideConsent(
            store: store, serverURL: scope.serverURL, userID: scope.userID, historyStart: narrower.historyStart), 0)
        XCTAssertNotNil(try store.load(otherUser).pending)
    }

    func testStateFilesFromEarlierBuildsStillDecode() throws {
        // Shape written by the first build: no scope, no unmappable, pending without
        // queryReturnedCount. Maintenance over all files must not fail on it.
        let legacy = """
        {"committedAnchor":null,"pending":{"payload":"e30=","candidateAnchor":"Mw==","sampleCount":2,
         "deletedCount":1,"createdAt":"2026-10-01T00:00:00.000Z"},
         "unsyncedDeletedCount":0,"totalInserted":0,"totalDuplicates":0}
        """
        let state = try ContractEncoding.decoder().decode(SyncState.self, from: Data(legacy.utf8))
        XCTAssertNil(state.scope)
        XCTAssertEqual(state.pending?.queryReturnedCount, 2)
        XCTAssertEqual(state.unmappable, [])
        // Legacy files without a scope are not touched by consent maintenance.
        let store = MemoryStore()
        store.states["legacy"] = state
        XCTAssertEqual(try SyncMaintenance.discardPendingOutsideConsent(
            store: store, serverURL: scope.serverURL, userID: scope.userID, historyStart: historyStart), 0)
    }
}
