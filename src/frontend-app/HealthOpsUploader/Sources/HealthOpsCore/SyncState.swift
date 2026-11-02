import Foundation

/// Identifies one independent sync stream. Anchors, pending pages and counters are keyed by
/// server + authenticated user + HealthKit type + the actual query configuration (history
/// start and configuration version). Switching account, server or consented history range
/// therefore never reuses another scope's anchor or queue: a narrower range cannot replay old
/// pending data, and a wider range starts from a fresh anchor and re-reads (the server answers
/// with duplicates for samples it already holds).
public struct SyncScope: Hashable, Codable, Sendable {
    public static let queryConfigurationVersion = "v2"

    public var serverURL: URL
    public var userID: String
    public var typeIdentifier: String
    /// Earliest sample start the user consented to upload; part of the query configuration.
    public var historyStart: Date

    public init(serverURL: URL, userID: String, typeIdentifier: String, historyStart: Date) {
        self.serverURL = serverURL
        self.userID = userID
        self.typeIdentifier = typeIdentifier
        self.historyStart = historyStart
    }

    /// Stable file-system-safe key.
    public var key: String {
        let raw = [
            serverURL.absoluteString, userID, typeIdentifier,
            ContractEncoding.timestamp(historyStart), Self.queryConfigurationVersion,
        ].joined(separator: "|")
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        return String(
            raw.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" }
        )
    }
}

/// A page that was read from HealthKit and persisted before upload. `payload` is the exact
/// request body; it is replayed byte-for-byte until the server confirms it.
public struct PendingPage: Codable, Equatable, Sendable {
    public var payload: Data
    public var candidateAnchor: Data?
    /// Observations inside `payload` (after consent filtering and mapping).
    public var sampleCount: Int
    /// Samples the device query returned for this page, before any filtering. This, not
    /// `sampleCount`, decides whether another page may follow.
    public var queryReturnedCount: Int
    public var deletedCount: Int
    /// Samples on this page that could not be mapped; recorded persistently when the page
    /// commits so they are never silently lost.
    public var unmappable: [UnmappableSample]
    public var createdAt: Date

    public init(
        payload: Data, candidateAnchor: Data?, sampleCount: Int, queryReturnedCount: Int,
        deletedCount: Int, unmappable: [UnmappableSample], createdAt: Date
    ) {
        self.payload = payload
        self.candidateAnchor = candidateAnchor
        self.sampleCount = sampleCount
        self.queryReturnedCount = queryReturnedCount
        self.deletedCount = deletedCount
        self.unmappable = unmappable
        self.createdAt = createdAt
    }

    /// Files written by earlier builds lack the newer fields; decode them with safe defaults
    /// so maintenance over all stored states never fails on an old file.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        payload = try c.decode(Data.self, forKey: .payload)
        candidateAnchor = try c.decodeIfPresent(Data.self, forKey: .candidateAnchor)
        sampleCount = try c.decode(Int.self, forKey: .sampleCount)
        queryReturnedCount = try c.decodeIfPresent(Int.self, forKey: .queryReturnedCount) ?? sampleCount
        deletedCount = try c.decodeIfPresent(Int.self, forKey: .deletedCount) ?? 0
        unmappable = try c.decodeIfPresent([UnmappableSample].self, forKey: .unmappable) ?? []
        createdAt = try c.decode(Date.self, forKey: .createdAt)
    }
}

/// A sample the mapper rejected. Kept per scope so the user can see what was not uploaded.
public struct UnmappableSample: Codable, Equatable, Sendable {
    public var uuid: UUID
    public var kind: String
    public var start: Date
    public var reason: String

    public init(uuid: UUID, kind: String, start: Date, reason: String) {
        self.uuid = uuid
        self.kind = kind
        self.start = start
        self.reason = reason
    }
}

public struct SyncState: Codable, Equatable, Sendable {
    /// The scope this state belongs to; lets the store enumerate states for a server and user.
    public var scope: SyncScope?
    /// Apple's anchor says how far the query has returned, not how far the server has saved.
    /// This field only advances after the server confirmed the page that produced it.
    public var committedAnchor: Data?
    public var pending: PendingPage?
    /// Samples that could not be mapped on committed pages. Not uploaded, not forgotten.
    public var unmappable: [UnmappableSample]
    /// Deletions HealthKit reported for committed pages. The server has no delete sync, so
    /// this count is kept persistently and shown; it never resets by itself.
    public var unsyncedDeletedCount: Int
    public var firstSyncStartedAt: Date?
    public var lastCommittedAt: Date?
    public var totalInserted: Int
    public var totalDuplicates: Int

    public init() {
        scope = nil
        committedAnchor = nil
        pending = nil
        unmappable = []
        unsyncedDeletedCount = 0
        firstSyncStartedAt = nil
        lastCommittedAt = nil
        totalInserted = 0
        totalDuplicates = 0
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        scope = try c.decodeIfPresent(SyncScope.self, forKey: .scope)
        committedAnchor = try c.decodeIfPresent(Data.self, forKey: .committedAnchor)
        pending = try c.decodeIfPresent(PendingPage.self, forKey: .pending)
        unmappable = try c.decodeIfPresent([UnmappableSample].self, forKey: .unmappable) ?? []
        unsyncedDeletedCount = try c.decodeIfPresent(Int.self, forKey: .unsyncedDeletedCount) ?? 0
        firstSyncStartedAt = try c.decodeIfPresent(Date.self, forKey: .firstSyncStartedAt)
        lastCommittedAt = try c.decodeIfPresent(Date.self, forKey: .lastCommittedAt)
        totalInserted = try c.decodeIfPresent(Int.self, forKey: .totalInserted) ?? 0
        totalDuplicates = try c.decodeIfPresent(Int.self, forKey: .totalDuplicates) ?? 0
    }
}

public protocol SyncStore: Sendable {
    func load(_ scope: SyncScope) throws -> SyncState
    func save(_ state: SyncState, for scope: SyncScope) throws
    /// Every stored state (for maintenance such as discarding pending pages of old consents).
    func allStates() throws -> [SyncState]
}

public enum SyncMaintenance {
    /// Discards pending pages that belong to the same server and user but to a different
    /// history start than the consent now in force. Such pages were read under an earlier
    /// consent and must not be replayed automatically. Returns the discarded page count.
    /// Anchors and counters of those scopes are left in place for inspection.
    @discardableResult
    public static func discardPendingOutsideConsent(
        store: SyncStore, serverURL: URL, userID: String, historyStart: Date
    ) throws -> Int {
        var discarded = 0
        for var state in try store.allStates() {
            guard let scope = state.scope, state.pending != nil,
                  scope.serverURL == serverURL, scope.userID == userID,
                  scope.historyStart != historyStart else { continue }
            state.pending = nil
            try store.save(state, for: scope)
            discarded += 1
        }
        return discarded
    }
}

/// One JSON file per scope inside a directory the app controls.
public final class FileSyncStore: SyncStore {
    private let directory: URL

    public init(directory: URL) throws {
        self.directory = directory
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    }

    private func url(for scope: SyncScope) -> URL {
        directory.appendingPathComponent("sync-\(scope.key).json")
    }

    public func load(_ scope: SyncScope) throws -> SyncState {
        let file = url(for: scope)
        guard FileManager.default.fileExists(atPath: file.path) else { return SyncState() }
        return try ContractEncoding.decoder().decode(SyncState.self, from: Data(contentsOf: file))
    }

    public func save(_ state: SyncState, for scope: SyncScope) throws {
        var state = state
        state.scope = scope
        let encoder = ContractEncoding.encoder()
        try encoder.encode(state).write(to: url(for: scope), options: .atomic)
    }

    public func allStates() throws -> [SyncState] {
        let files = try FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil)
        return try files.filter { $0.lastPathComponent.hasPrefix("sync-") }.map {
            try ContractEncoding.decoder().decode(SyncState.self, from: Data(contentsOf: $0))
        }
    }
}
