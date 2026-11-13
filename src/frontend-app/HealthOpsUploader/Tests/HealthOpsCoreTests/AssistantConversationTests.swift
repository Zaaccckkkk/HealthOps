import Foundation
import XCTest
@testable import HealthOpsCore

final class AssistantConversationTests: XCTestCase {
    func testLateResponseAfterResetIsDropped() {
        var conversation = AssistantConversation()
        let ticket = conversation.begin(question: "steps yesterday?", provider: nil)
        conversation.reset()  // token, server or account changed while the request was in flight
        XCTAssertFalse(conversation.complete(ticket, with: .failure(.server(500))))
        XCTAssertTrue(conversation.turns.isEmpty)
        XCTAssertNil(conversation.inFlight)
    }

    func testOutcomeAttachesToItsOwnTurnOnly() {
        var conversation = AssistantConversation()
        let first = conversation.begin(question: "a", provider: "healthkit")
        let second = conversation.begin(question: "b", provider: nil)
        XCTAssertTrue(conversation.complete(second, with: .failure(.unauthorized)))
        XCTAssertNil(conversation.turns[0].outcome)
        XCTAssertEqual(conversation.turns[1].outcome, .failure(.unauthorized))
        XCTAssertTrue(conversation.complete(first, with: .failure(.rateLimited)))
        XCTAssertEqual(conversation.turns[0].outcome, .failure(.rateLimited))
        XCTAssertNil(conversation.inFlight)
    }

    func testEachQuestionIsIndependentOfTheHistory() {
        // The conversation never produces anything to send: the request is the question alone.
        var conversation = AssistantConversation()
        _ = conversation.begin(question: "first", provider: nil)
        let request = AskRequest(question: "second", timezone: "UTC", provider: nil)
        XCTAssertEqual(request.question, "second")
        XCTAssertEqual(conversation.turns.count, 1)
    }

    func testFailureMessagesNameTheUnavailableService() {
        XCTAssertTrue(ServiceFailure.unavailable("HTTP 503").message.contains("unavailable"))
        XCTAssertTrue(ServiceFailure.unavailable("Could not connect").message.contains("Could not connect"))
    }

    func testChannelRoundTripPreservesHistoryAndOriginalSourceLabels() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(
            #"{"status":"answered","explanation_status":"verified","answer":"Synthetic answer."}"#.utf8))
        var conversation = AssistantConversation()
        let all = conversation.begin(question: "All sources?", provider: nil)
        conversation.complete(all, with: .response(response))
        let original = conversation.turns[0]
        conversation.endContinuation() // All channels -> Apple Health
        let apple = conversation.begin(question: "Apple only?", provider: "healthkit")
        conversation.complete(apple, with: .response(response))
        conversation.endContinuation() // Apple Health -> Google Health
        let google = conversation.begin(question: "Google only?", provider: "google_health")
        conversation.complete(google, with: .response(response))
        conversation.endContinuation() // Google Health -> All channels
        XCTAssertEqual(conversation.turns.count, 3)
        XCTAssertEqual(conversation.turns[0], original)
        XCTAssertEqual(conversation.turns.map(\.provider), [nil, "healthkit", "google_health"])
        XCTAssertTrue(conversation.turns.allSatisfy { $0.outcome == .response(response) })
    }

    func testChannelChangeKeepsClarificationTextButDoesNotResumeOldScope() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(
            #"{"status":"needs_clarification","explanation_status":"not_applicable","answer":"Which dates?","conversation_id":"synthetic-scope","conversation_version":2}"#.utf8))
        var conversation = AssistantConversation()
        let ticket = conversation.begin(question: "Recently?", provider: nil)
        conversation.complete(ticket, with: .response(response))
        let original = conversation.turns
        conversation.endContinuation()
        XCTAssertEqual(conversation.turns, original)
        let request = AskRequest(question: "Steps yesterday", timezone: "UTC", provider: "healthkit",
                                 conversationID: conversation.conversationID,
                                 conversationVersion: conversation.conversationVersion)
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as! [String: Any]
        XCTAssertEqual(encoded["provider"] as? String, "healthkit")
        XCTAssertNil(encoded["conversation_id"])
        XCTAssertNil(encoded["conversation_version"])
    }

    func testChannelChangeCancelsWaitingTurnAndRejectsLateReplyDuringNewRequest() {
        var conversation = AssistantConversation()
        let old = conversation.begin(question: "All sources?", provider: nil)
        conversation.endContinuation()
        XCTAssertEqual(conversation.turns[0].outcome, .failure(.cancelled))
        XCTAssertNil(conversation.inFlight)
        let current = conversation.begin(question: "Google only?", provider: "google_health")
        XCTAssertFalse(conversation.complete(old, with: .failure(.server(500))))
        XCTAssertEqual(conversation.inFlight, current)
        XCTAssertNil(conversation.turns[1].outcome)
        XCTAssertTrue(conversation.complete(current, with: .failure(.rateLimited)))
        conversation.reset() // Identity changes must still clear all retained sources.
        XCTAssertTrue(conversation.turns.isEmpty)
    }
}

extension AssistantConversationTests {
    func testClarificationContinuationIsDecodedCarriedAndReset() throws {
        let json = #"{"status":"needs_clarification","explanation_status":"not_applicable","answer":"Which dates?","conversation_id":"synthetic-conversation","conversation_version":1}"#
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(json.utf8))
        var conversation = AssistantConversation()
        let ticket = conversation.begin(question: "recently?", provider: nil)
        XCTAssertTrue(conversation.complete(ticket, with: .response(response)))
        let reply = AskRequest(question: "7 days", timezone: "UTC", provider: nil,
                               conversationID: conversation.conversationID,
                               conversationVersion: conversation.conversationVersion)
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(reply)) as! [String: Any]
        XCTAssertEqual(encoded["conversation_id"] as? String, "synthetic-conversation")
        XCTAssertEqual(encoded["conversation_version"] as? Int, 1)
        conversation.reset()
        XCTAssertNil(conversation.conversationID)
        XCTAssertFalse(conversation.complete(ticket, with: .response(response)))
        XCTAssertNil(conversation.conversationID)
    }
}
