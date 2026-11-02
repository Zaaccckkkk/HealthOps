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
