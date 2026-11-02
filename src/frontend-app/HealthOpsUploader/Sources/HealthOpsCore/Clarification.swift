import Foundation

public struct ClarificationOption: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var label: String
    public var reply_text: String
}

public struct ClarificationField: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var label: String
    public var selection: String
    public var options: [ClarificationOption]
    public var allows_custom: Bool
}

public struct ClarificationForm: Codable, Equatable, Sendable {
    public var version: Int
    public var fields: [ClarificationField]
}

/// Selections are user-authored text, not privileged tool arguments. The router validates again.
public struct ClarificationDraft: Equatable, Sendable {
    public var selected: [String: Set<String>] = [:]
    public var custom = ""
    public init() {}

    public mutating func toggle(_ option: ClarificationOption, in field: ClarificationField) {
        var values = selected[field.id] ?? []
        if values.contains(option.id) { values.remove(option.id) }
        else if field.selection == "multiple" { values.insert(option.id) }
        else { values = [option.id] }
        selected[field.id] = values
    }

    public func reply(for form: ClarificationForm) -> String {
        guard form.version == 1 else { return custom.trimmingCharacters(in: .whitespacesAndNewlines) }
        let parts = form.fields.compactMap { field -> String? in
            let values = field.options.filter { selected[field.id, default: []].contains($0.id) }
                .map(\.reply_text)
            return values.isEmpty ? nil : values.joined(separator: ", ")
        }
        let text = custom.trimmingCharacters(in: .whitespacesAndNewlines)
        return (parts + (text.isEmpty ? [] : ["Additional details or correction: \(text)"]))
            .joined(separator: "; ")
    }
}

/// Only a pending question/clarification is persisted; completed evidence and tokens are excluded.
/// The app stores this data in device-only Keychain, never UserDefaults.
public struct PendingClarification: Codable, Equatable, Sendable {
    public let scope: String
    public let question: String
    public let responseData: Data
    public let expiresAt: Date

    public init?(scope: String, question: String, response: AskResponse) {
        guard response.status == .needsClarification,
              let cid = response.conversationID, let version = response.conversationVersion,
              let expiration = response.conversationExpiresAt,
              let expires = Self.date(expiration) else { return nil }
        struct Snapshot: Encodable {
            var status = "needs_clarification"
            var explanation_status = "not_applicable"
            let answer: String
            let conversation_id: String
            let conversation_version: Int
            let conversation_expires_at: String
            let clarification: ClarificationForm?
        }
        let snapshot = Snapshot(answer: response.answer, conversation_id: cid,
                                conversation_version: version, conversation_expires_at: expiration,
                                clarification: response.clarification)
        guard let bytes = try? JSONEncoder().encode(snapshot) else { return nil }
        self.scope = scope
        self.question = question
        self.responseData = bytes
        self.expiresAt = expires
    }

    public func response(for scope: String, now: Date = Date()) -> AskResponse? {
        guard self.scope == scope, expiresAt > now else { return nil }
        return try? JSONDecoder().decode(AskResponse.self, from: responseData)
    }

    private static func date(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }
}
