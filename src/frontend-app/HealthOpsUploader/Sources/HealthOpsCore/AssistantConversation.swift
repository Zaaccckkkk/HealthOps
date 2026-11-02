import Foundation

/// Why a request produced no result. Network failures and 503 both mean "the server is not
/// reachable or not ready"; when the cloud VM is stopped the phone sees one of these.
public enum ServiceFailure: Error, Equatable, Sendable {
    case unavailable(String)
    case unauthorized
    case rateLimited
    case invalidRequest(String)
    case server(Int)
    case decoding(String)
    case cancelled

    public var message: String {
        switch self {
        case let .unavailable(detail): return "Service unavailable: \(detail)"
        case .unauthorized: return "The token was rejected. Save a valid token and connect again."
        case .rateLimited: return "Too many questions in a short time. Wait a minute and retry."
        case let .invalidRequest(detail): return "The server rejected the question: \(detail)"
        case let .server(status): return "Server error (HTTP \(status))."
        case let .decoding(detail): return "The response could not be read: \(detail)"
        case .cancelled: return "Cancelled."
        }
    }
}

/// Screen history plus a server-issued clarification continuation. Reset on scope changes.
public struct AssistantConversation: Equatable, Sendable {
    public enum Outcome: Equatable, Sendable {
        case response(AskResponse)
        case failure(ServiceFailure)
    }

    public struct Turn: Equatable, Sendable, Identifiable {
        public let id: UUID
        public let question: String
        public let provider: String?
        public let askedAt: Date
        public internal(set) var outcome: Outcome?
    }

    public struct Ticket: Equatable, Sendable {
        public let generation: Int
        public let turnID: UUID
    }

    public private(set) var conversationID: String?
    public private(set) var conversationVersion: Int?

    public private(set) var turns: [Turn] = []
    public private(set) var generation = 0
    public private(set) var inFlight: Ticket?

    public init() {}

    /// Appends the question and returns the ticket its answer must present.
    public mutating func begin(question: String, provider: String?, now: Date = Date()) -> Ticket {
        let turn = Turn(id: UUID(), question: question, provider: provider, askedAt: now, outcome: nil)
        turns.append(turn)
        let ticket = Ticket(generation: generation, turnID: turn.id)
        inFlight = ticket
        return ticket
    }

    /// Stores the outcome only if nothing was reset since the question was asked.
    @discardableResult
    public mutating func complete(_ ticket: Ticket, with outcome: Outcome) -> Bool {
        guard ticket.generation == generation,
              let index = turns.firstIndex(where: { $0.id == ticket.turnID }),
              turns[index].outcome == nil else { return false }
        if inFlight == ticket, case let .response(response) = outcome {
            conversationID = response.conversationID
            conversationVersion = response.conversationVersion
        } else if inFlight == ticket {
            // A lost HTTP response may already have consumed the continuation. Start fresh.
            conversationID = nil
            conversationVersion = nil
        }
        turns[index].outcome = outcome
        if inFlight == ticket { inFlight = nil }
        return true
    }

    /// Clears the history and invalidates every outstanding ticket. Call on server, token or
    /// account change; the caller cancels the running task as well.
    public mutating func reset() {
        conversationID = nil
        conversationVersion = nil
        generation += 1
        turns.removeAll()
        inFlight = nil
    }
}
