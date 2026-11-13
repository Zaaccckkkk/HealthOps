import CryptoKit
import Foundation
import HealthOpsCore
import SwiftUI

/// Phone chat state. Clarification replies carry a server-issued continuation.
/// Reset history on identity changes. Provider changes retain history but invalidate the
/// old clarification and in-flight work so replies cannot cross data-source scopes.
@MainActor
final class AssistantModel: ObservableObject {
    enum ProviderChoice: String, CaseIterable, Identifiable {
        case healthkit
        case googleHealth = "google_health"
        case all
        var id: String { rawValue }
        var label: String { self == .all ? "All channels" : ProviderLabel.display(rawValue) }
        /// The request value: nil means every channel.
        var requestValue: String? { self == .all ? nil : rawValue }
    }

    @Published private(set) var conversation = AssistantConversation()
    @Published var questionDraft = ""
    @Published var provider: ProviderChoice = .all {
        didSet { if oldValue != provider { changeProvider() } }
    }
    @Published private(set) var busy = false
    @Published private(set) var notice: String?

    private var task: Task<Void, Never>?
    private var currentHistoryScope: String?
    private var restorationAttempted = false

    var pendingResponse: AskResponse? {
        guard conversation.conversationID != nil,
              case let .response(response) = turns.last?.outcome else { return nil }
        return response
    }

    private func scope(serverURL: URL, token: String, includeProvider: Bool = true) -> String {
        let credential = SHA256.hash(data: Data(token.utf8)).map { String(format: "%02x", $0) }.joined()
        var parts = [serverURL.absoluteString, credential]
        if includeProvider { parts.append(provider.rawValue) }
        parts.append(TimeZone.current.identifier)
        return parts.joined(separator: "|")
    }

    func restore(serverURL: URL, token: String) {
        let key = scope(serverURL: serverURL, token: token)
        let historyKey = scope(serverURL: serverURL, token: token, includeProvider: false)
        if let old = currentHistoryScope, old != historyKey { reset(reason: "request scope changed") }
        currentHistoryScope = historyKey
        guard !restorationAttempted, turns.isEmpty, !busy else { return }
        restorationAttempted = true
        guard let data = ClarificationKeychain.load() else { return }
        guard let saved = try? JSONDecoder().decode(PendingClarification.self, from: data),
              let response = saved.response(for: key) else {
            ClarificationKeychain.save(nil)
            notice = "Previous clarification expired or belongs to a different account or scope. Start a new question."
            return
        }
        let ticket = conversation.begin(question: saved.question, provider: provider.requestValue)
        conversation.complete(ticket, with: .response(response))
        notice = "Restored your pending clarification. Completed answers are not stored on this device."
    }

    var turns: [AssistantConversation.Turn] { conversation.turns }

    private func changeProvider() {
        task?.cancel()
        task = nil
        let wasClarifying = conversation.conversationID != nil
        conversation.endContinuation()
        restorationAttempted = true
        ClarificationKeychain.save(nil)
        busy = false
        // A draft clarification answer such as "seven days" must not become a new
        // standalone question under a different source. Ordinary drafts remain editable.
        if wasClarifying { questionDraft = "" }
        notice = "Data source changed. History kept; new questions use \(provider.label)."
        if wasClarifying {
            notice = "Data source changed. History kept; start a new question for \(provider.label) instead of continuing the previous clarification."
        }
    }

    /// Drops the history and invalidates in-flight work. Called by AppModel on scope changes.
    func reset(reason: String) {
        task?.cancel()
        task = nil
        conversation.reset()
        restorationAttempted = true
        questionDraft = ""
        ClarificationKeychain.save(nil)
        busy = false
        notice = "History cleared (\(reason)). Pending server clarification state expires within one hour."
    }

    func ask(serverURL: URL, token: String) {
        let draft = questionDraft
        restore(serverURL: serverURL, token: token)
        let question = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !busy else { return }
        guard ClarificationKeychain.save(nil) else {
            notice = "Could not clear saved clarification. Unlock the device and retry."
            return
        }
        questionDraft = ""
        notice = nil
        let ticket = conversation.begin(question: question, provider: provider.requestValue)
        let request = AskRequest(
            question: question, timezone: TimeZone.current.identifier, provider: provider.requestValue,
            conversationID: conversation.conversationID,
            conversationVersion: conversation.conversationVersion)
        let requestScope = scope(serverURL: serverURL, token: token)
        let client = APIClient(baseURL: serverURL, token: token)
        busy = true
        task = Task { [weak self] in
            let outcome: AssistantConversation.Outcome
            do {
                outcome = .response(try await client.ask(request))
            } catch let failure as ServiceFailure {
                outcome = .failure(failure)
            } catch {
                outcome = .failure(.unavailable((error as NSError).localizedDescription))
            }
            await MainActor.run {
                guard let self else { return }
                guard self.conversation.complete(ticket, with: outcome) else { return }
                if self.conversation.inFlight == nil {
                    self.busy = false
                    self.task = nil
                    if case let .response(response) = outcome,
                       let checkpoint = PendingClarification(
                           scope: requestScope,
                           question: question, response: response),
                       let data = try? JSONEncoder().encode(checkpoint) {
                        if !ClarificationKeychain.save(data) {
                            self.notice = "Reply here before closing the app; this clarification could not be saved."
                        }
                    } else if case .failure = outcome {
                        self.notice = "The server may already have processed this request. Edit and send a new question."
                        self.questionDraft = question
                    }
                }
            }
        }
    }

    func cancel() {
        task?.cancel()
        task = nil
        if let ticket = conversation.inFlight {
            conversation.complete(ticket, with: .failure(.cancelled))
        }
        ClarificationKeychain.save(nil)
        busy = false
        notice = "Stopped waiting. The server may still finish; start a new question."
    }
}
