import Foundation
import HealthOpsCore
import SwiftUI

/// Chat state for the phone. Every question is one independent `/v1/assistant/ask` request
/// under the verified identity; the history lives only in memory. Server, token or account
/// changes call `reset(reason:)`, which drops the history, cancels the running request and makes
/// any late response stale. Nothing here reads the debug fields: the decoded response has none.
@MainActor
final class AssistantModel: ObservableObject {
    enum ProviderChoice: String, CaseIterable, Identifiable {
        case healthkit
        case all
        var id: String { rawValue }
        var label: String { self == .healthkit ? "HealthKit" : "All channels" }
        /// The request value: nil means every channel.
        var requestValue: String? { self == .healthkit ? "healthkit" : nil }
    }

    @Published private(set) var conversation = AssistantConversation()
    @Published var questionDraft = ""
    @Published var provider: ProviderChoice = .healthkit
    @Published private(set) var busy = false
    @Published private(set) var notice: String?

    private var task: Task<Void, Never>?

    var turns: [AssistantConversation.Turn] { conversation.turns }

    /// Drops the history and invalidates in-flight work. Called by AppModel on scope changes.
    func reset(reason: String) {
        task?.cancel()
        task = nil
        conversation.reset()
        busy = false
        notice = "History cleared (\(reason)). Questions are answered one at a time; nothing was stored."
    }

    func ask(serverURL: URL, token: String) {
        let question = questionDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !question.isEmpty, !busy else { return }
        questionDraft = ""
        notice = nil
        let ticket = conversation.begin(question: question, provider: provider.requestValue)
        let request = AskRequest(
            question: question, timezone: TimeZone.current.identifier, provider: provider.requestValue)
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
                // A response for a previous generation (server, token or account changed) is
                // dropped here; `complete` refuses it.
                if !self.conversation.complete(ticket, with: outcome) {
                    self.notice = "Ignored a late response that belonged to a previous server, token or account."
                }
                if self.conversation.inFlight == nil { self.busy = false }
                self.task = nil
            }
        }
    }

    func cancel() {
        task?.cancel()
    }
}
