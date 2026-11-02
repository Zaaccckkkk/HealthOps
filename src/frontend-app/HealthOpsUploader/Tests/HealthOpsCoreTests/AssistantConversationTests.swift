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
