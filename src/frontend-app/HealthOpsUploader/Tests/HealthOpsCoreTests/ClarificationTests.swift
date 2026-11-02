import Foundation
import XCTest
@testable import HealthOpsCore

final class ClarificationTests: XCTestCase {
    static let json = #"{"status":"needs_clarification","explanation_status":"not_applicable","answer":"Which scope?","conversation_id":"synthetic-session","conversation_version":2,"conversation_expires_at":"2026-11-01T20:00:00.123456+00:00","clarification":{"version":1,"fields":[{"id":"metrics","label":"Metrics?","selection":"multiple","allows_custom":true,"options":[{"id":"steps","label":"Steps","reply_text":"steps"},{"id":"sleep","label":"Sleep duration","reply_text":"sleep_duration"}]},{"id":"time_range","label":"When?","selection":"single","allows_custom":true,"options":[{"id":"seven","label":"7 days","reply_text":"past 7 days"},{"id":"thirty","label":"30 days","reply_text":"past 30 days"}]}]}}"#

    func testSelectionsAreExplicitAndCustomCorrectionIsPreserved() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(Self.json.utf8))
        let form = try XCTUnwrap(response.clarification)
        var draft = ClarificationDraft()
        XCTAssertEqual(draft.reply(for: form), "")
        let metric = form.fields[0], time = form.fields[1]
        draft.toggle(metric.options[0], in: metric)
        draft.toggle(metric.options[1], in: metric)
        draft.toggle(time.options[0], in: time)
        draft.toggle(time.options[1], in: time)
        draft.custom = "Actually steps only"
        XCTAssertEqual(draft.reply(for: form), "steps, sleep_duration; past 30 days; Additional details or correction: Actually steps only")
        XCTAssertFalse(draft.reply(for: form).contains("past 7 days"))
    }

    func testCheckpointRestoresOnlyMatchingUnexpiredScopeAndContainsNoEvidence() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(Self.json.utf8))
        let saved = try XCTUnwrap(PendingClarification(scope: "synthetic-scope", question: "recently?", response: response))
        let before = saved.expiresAt.addingTimeInterval(-60)
        XCTAssertNotNil(saved.response(for: "synthetic-scope", now: before))
        XCTAssertNil(saved.response(for: "another-account", now: before))
        XCTAssertNil(saved.response(for: "synthetic-scope", now: saved.expiresAt))
        let restored = try JSONDecoder().decode(PendingClarification.self, from: JSONEncoder().encode(saved))
        XCTAssertEqual(restored, saved)
        let text = String(decoding: saved.responseData, as: UTF8.self)
        XCTAssertFalse(text.contains("cards"))
        XCTAssertFalse(text.contains("model_text"))
    }
}

extension ClarificationTests {
    func testDuplicateAndOutOfOrderCompletionCannotReplaceContinuation() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(Self.json.utf8))
        var conversation = AssistantConversation()
        let old = conversation.begin(question: "old", provider: nil)
        let current = conversation.begin(question: "new", provider: nil)
        XCTAssertTrue(conversation.complete(current, with: .response(response)))
        XCTAssertFalse(conversation.complete(current, with: .failure(.server(500))))
        XCTAssertTrue(conversation.complete(old, with: .failure(.cancelled)))
        XCTAssertEqual(conversation.conversationID, "synthetic-session")
        XCTAssertEqual(conversation.conversationVersion, 2)
    }

    func testFailureConsumesLocalContinuationAndLateCancelledResponseIsIgnored() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(Self.json.utf8))
        var conversation = AssistantConversation()
        let first = conversation.begin(question: "recently", provider: nil)
        conversation.complete(first, with: .response(response))
        let reply = conversation.begin(question: "past 7 days", provider: nil)
        conversation.complete(reply, with: .failure(.unavailable("synthetic timeout")))
        XCTAssertNil(conversation.conversationID)
        XCTAssertFalse(conversation.complete(reply, with: .response(response)))
        XCTAssertNil(conversation.conversationID)
    }

    func testUnknownFormVersionFallsBackToCustomTextAndTogglingClearsSelection() throws {
        var form = try XCTUnwrap(JSONDecoder().decode(AskResponse.self, from: Data(Self.json.utf8)).clarification)
        var draft = ClarificationDraft()
        draft.toggle(form.fields[0].options[0], in: form.fields[0])
        draft.toggle(form.fields[0].options[0], in: form.fields[0])
        XCTAssertEqual(draft.reply(for: form), "")
        draft.toggle(form.fields[0].options[0], in: form.fields[0])
        form.version = 2
        draft.custom = "Yesterday, steps only"
        XCTAssertEqual(draft.reply(for: form), "Yesterday, steps only")
    }

    func testOldServerResponseDoesNotCreatePersistedCheckpoint() throws {
        let response = try JSONDecoder().decode(AskResponse.self, from: Data(#"{"status":"needs_clarification","explanation_status":"not_applicable","answer":"Which dates?","conversation_id":"old-server","conversation_version":1}"#.utf8))
        XCTAssertNil(response.clarification)
        XCTAssertNil(PendingClarification(scope: "scope", question: "recently", response: response))
    }
}
