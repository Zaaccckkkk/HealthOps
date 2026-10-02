import Foundation
import XCTest
@testable import HealthOpsCore

final class IdentitySessionTests: XCTestCase {
    let serverA = URL(string: "http://127.0.0.1:8000")!
    let serverB = URL(string: "http://10.0.0.5:8000")!
    let userA = UserInfo(id: "user-a", displayName: "A")
    let userB = UserInfo(id: "user-b", displayName: "B")

    func testStaleResponseAfterCredentialChangeIsIgnored() {
        var session = IdentitySession()
        session.serverChanged(to: serverA)
        session.credentialsChanged()  // token A stored
        let attemptA = session.beginVerification()!
        session.credentialsChanged()  // token B stored while /v1/me for A is in flight
        XCTAssertFalse(session.complete(attemptA, user: userA))
        XCTAssertNil(session.current)
        let attemptB = session.beginVerification()!
        XCTAssertTrue(session.complete(attemptB, user: userB))
        XCTAssertEqual(session.current?.user, userB)
    }

    func testChangingServerClearsVerifiedIdentity() {
        var session = IdentitySession()
        session.serverChanged(to: serverA)
        session.complete(session.beginVerification()!, user: userA)
        XCTAssertEqual(session.current?.user, userA)
        XCTAssertTrue(session.serverChanged(to: serverB))
        XCTAssertNil(session.current)  // consent and sync must be refused now
        // A late response for server A is ignored too.
        let stale = IdentitySession.Attempt(serverURL: serverA, credentialVersion: session.credentialVersion)
        XCTAssertFalse(session.complete(stale, user: userA))
        XCTAssertNil(session.current)
    }

    func testUnchangedServerTextDoesNotInvalidate() {
        var session = IdentitySession()
        session.serverChanged(to: serverA)
        session.complete(session.beginVerification()!, user: userA)
        XCTAssertFalse(session.serverChanged(to: serverA))
        XCTAssertEqual(session.current?.user, userA)
    }

    func testNoVerificationWithoutServer() {
        XCTAssertNil(IdentitySession().beginVerification())
    }
}
