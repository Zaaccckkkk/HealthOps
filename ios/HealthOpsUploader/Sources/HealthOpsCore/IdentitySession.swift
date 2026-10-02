import Foundation

/// Tracks which server and credential version an identity was verified against.
///
/// The verified user is only meaningful for the exact server URL and credential that produced
/// it. Changing either one clears the identity and invalidates any in-flight verification, so a
/// late `/v1/me` response for an old token can never be attached to a new one, and consent can
/// never be granted or a sync started against an unverified server/credential pair.
public struct IdentitySession: Equatable, Sendable {
    public struct Verified: Equatable, Sendable {
        public let serverURL: URL
        public let credentialVersion: Int
        public let user: UserInfo
    }

    public struct Attempt: Equatable, Sendable {
        public let serverURL: URL
        public let credentialVersion: Int
    }

    public private(set) var serverURL: URL?
    public private(set) var credentialVersion = 0
    public private(set) var verified: Verified?

    public init() {}

    /// Call on every server URL change. Returns true when a verified identity was invalidated.
    @discardableResult
    public mutating func serverChanged(to url: URL?) -> Bool {
        guard url != serverURL else { return false }
        serverURL = url
        return invalidate()
    }

    /// Call whenever the stored credential changes (saved, replaced or cleared).
    @discardableResult
    public mutating func credentialsChanged() -> Bool {
        credentialVersion += 1
        return invalidate()
    }

    private mutating func invalidate() -> Bool {
        let had = verified != nil
        verified = nil
        return had
    }

    /// Snapshot taken before calling `/v1/me`; the response is accepted only if nothing changed.
    public func beginVerification() -> Attempt? {
        guard let serverURL else { return nil }
        return Attempt(serverURL: serverURL, credentialVersion: credentialVersion)
    }

    /// Returns false (and stores nothing) for a response that belongs to an earlier attempt.
    @discardableResult
    public mutating func complete(_ attempt: Attempt, user: UserInfo) -> Bool {
        guard attempt.serverURL == serverURL, attempt.credentialVersion == credentialVersion
        else { return false }
        verified = Verified(serverURL: attempt.serverURL, credentialVersion: attempt.credentialVersion, user: user)
        return true
    }

    /// The identity usable right now: verified against the current server and credential.
    public var current: Verified? {
        guard let verified, verified.serverURL == serverURL,
              verified.credentialVersion == credentialVersion else { return nil }
        return verified
    }
}
