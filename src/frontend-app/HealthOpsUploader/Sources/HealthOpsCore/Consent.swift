import Foundation

/// Explicit upload consent, distinct from HealthKit read authorization. HealthKit authorization
/// lets the app read on the phone; this record is what allows sending data off the phone, and
/// it is bound to one server and one authenticated user.
public struct ConsentRecord: Codable, Equatable, Sendable {
    public static let explanationVersion = 1

    public var serverURL: URL
    public var userID: String
    public var enabledMetrics: Set<Metric>
    /// Earliest sample start the user agreed to upload; shown before the first sync.
    public var historyStart: Date
    public var grantedAt: Date
    public var explanationVersion: Int

    public init(
        serverURL: URL, userID: String, enabledMetrics: Set<Metric>, historyStart: Date,
        grantedAt: Date
    ) {
        self.serverURL = serverURL
        self.userID = userID
        self.enabledMetrics = enabledMetrics
        self.historyStart = historyStart
        self.grantedAt = grantedAt
        explanationVersion = Self.explanationVersion
    }

    /// Consent applies only to the server and user it was granted for.
    public func covers(serverURL: URL, userID: String) -> Bool {
        self.serverURL == serverURL && self.userID == userID
            && explanationVersion == Self.explanationVersion
    }
}

public protocol ConsentStore: Sendable {
    func load() throws -> ConsentRecord?
    func save(_ record: ConsentRecord?) throws
}

public final class FileConsentStore: ConsentStore {
    private let file: URL

    public init(file: URL) throws {
        self.file = file
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
    }

    public func load() throws -> ConsentRecord? {
        guard FileManager.default.fileExists(atPath: file.path) else { return nil }
        return try ContractEncoding.decoder().decode(
            ConsentRecord.self, from: Data(contentsOf: file))
    }

    public func save(_ record: ConsentRecord?) throws {
        guard let record else {
            try? FileManager.default.removeItem(at: file)
            return
        }
        try ContractEncoding.encoder().encode(record).write(to: file, options: .atomic)
    }
}
