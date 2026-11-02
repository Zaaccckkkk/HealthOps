import Foundation

// Swift mirror of the Google Health connection routes. The phone never sees Google tokens:
// it asks the server for an authorization URL, opens it in a browser session, and reads the
// outcome back from the server with its own bearer token.

public struct ConnectionSessionResponse: Decodable, Equatable, Sendable {
    public var authorizationURL: URL
    public var expiresInSeconds: Int
    enum CodingKeys: String, CodingKey { case authorizationURL = "authorization_url", expiresInSeconds = "expires_in_seconds" }
}

public struct ConnectionStatus: Decodable, Equatable, Sendable {
    public var configured: Bool
    public var connected: Bool
    public var connectedAt: String?
    public var lastSyncAt: String?
    public var scopes: [String]
    public var linkedAccount: Bool?
    public var lastSyncReport: ConnectorSyncReport?
    public var lastSessionOutcome: String?

    enum CodingKeys: String, CodingKey {
        case configured, connected, scopes
        case connectedAt = "connected_at", lastSyncAt = "last_sync_at", linkedAccount = "linked_account"
        case lastSyncReport = "last_sync_report", lastSessionOutcome = "last_session_outcome"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        configured = try c.decodeIfPresent(Bool.self, forKey: .configured) ?? false
        connected = try c.decodeIfPresent(Bool.self, forKey: .connected) ?? false
        connectedAt = try c.decodeIfPresent(String.self, forKey: .connectedAt)
        lastSyncAt = try c.decodeIfPresent(String.self, forKey: .lastSyncAt)
        scopes = try c.decodeIfPresent([String].self, forKey: .scopes) ?? []
        linkedAccount = try c.decodeIfPresent(Bool.self, forKey: .linkedAccount)
        lastSyncReport = try? c.decodeIfPresent(ConnectorSyncReport.self, forKey: .lastSyncReport)
        lastSessionOutcome = try c.decodeIfPresent(String.self, forKey: .lastSessionOutcome)
    }
}

public struct ConnectorSyncReport: Decodable, Equatable, Sendable {
    public struct TypeReport: Decodable, Equatable, Sendable, Identifiable {
        public var id: String { dataType }
        public var dataType: String
        public var fetched: Int
        public var inserted: Int
        public var duplicates: Int
        public var conflicts: [String]
        public var unmapped: Int
        public var skippedOtherPlatform: Int
        public var warnings: [String]
        public var error: String?
        enum CodingKeys: String, CodingKey {
            case dataType = "data_type", fetched, inserted, duplicates, conflicts, unmapped, error, warnings
            case skippedOtherPlatform = "skipped_other_platform"
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            dataType = try c.decode(String.self, forKey: .dataType)
            fetched = try c.decodeIfPresent(Int.self, forKey: .fetched) ?? 0
            inserted = try c.decodeIfPresent(Int.self, forKey: .inserted) ?? 0
            duplicates = try c.decodeIfPresent(Int.self, forKey: .duplicates) ?? 0
            conflicts = try c.decodeIfPresent([String].self, forKey: .conflicts) ?? []
            unmapped = try c.decodeIfPresent(Int.self, forKey: .unmapped) ?? 0
            skippedOtherPlatform = try c.decodeIfPresent(Int.self, forKey: .skippedOtherPlatform) ?? 0
            warnings = try c.decodeIfPresent([String].self, forKey: .warnings) ?? []
            error = try c.decodeIfPresent(String.self, forKey: .error)
        }
    }

    public var windowStart: String
    public var windowEnd: String
    public var timezone: String
    public var types: [TypeReport]
    enum CodingKeys: String, CodingKey { case windowStart = "window_start", windowEnd = "window_end", timezone, types }

    public var inserted: Int { types.reduce(0) { $0 + $1.inserted } }
    public var conflicts: Int { types.reduce(0) { $0 + $1.conflicts.count } }
    public var failedTypes: [String] { types.filter { $0.error != nil }.map(\.dataType) }
}

public struct DisconnectResult: Decodable, Equatable, Sendable {
    public var disconnected: Bool
    public var revokedAtProvider: Bool
    enum CodingKeys: String, CodingKey { case disconnected, revokedAtProvider = "revoked_at_provider" }
}

public struct DeleteDataResult: Decodable, Equatable, Sendable {
    public var deleted: Int
}

/// Display names for data channels. A channel is not a single device: Apple Health can hold
/// Apple Watch and third-party data; Google Health holds whatever the Google account syncs.
public enum ProviderLabel {
    public static func display(_ provider: String?) -> String {
        switch provider {
        case "healthkit": return "Apple Health"
        case "google_health": return "Google Health"
        case nil: return "All channels"
        default: return provider!
        }
    }
}

/// Human text for the status word the callback appends to the app's URL scheme.
public enum ConnectionOutcome {
    public static func describe(_ status: String?) -> String {
        switch status {
        case "connected": return "Google Health connected."
        case "denied": return "Authorization was declined in the browser."
        case "account_not_linked": return "This Google Account is not linked to Google Health (Fitbit)."
        case "no_refresh_token": return "Google did not grant offline access; try again and accept all requested access."
        case "token_exchange_failed": return "Google rejected the authorization code; try again."
        case "provider_unavailable": return "Google was unavailable; try again later."
        case "invalid_state": return "The authorization link was expired or already used; start again."
        case let other?: return "Authorization ended with status \(other)."
        case nil: return "No authorization result."
        }
    }
}
