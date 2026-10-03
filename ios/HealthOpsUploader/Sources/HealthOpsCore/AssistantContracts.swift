import Foundation

// Swift mirror of the Assistant v0 response (docs/api-contract.md). Only what the phone shows
// is decoded: data status, explanation status, the template answer, the verified claims, the
// deterministic cards and the disclaimer. Debug fields (`model_text`, `tool_calls`, `evidence`,
// `model`, `limits`, `numeric_consistency`) are deliberately not decoded, so no code path in
// the app can display model prose or raw tool arguments. Every enum keeps an `unknown` case:
// a new server value must never crash decoding, and `unknown` is never shown as success.

public struct AskRequest: Encodable, Equatable, Sendable {
    public var question: String
    public var timezone: String
    /// Data channel chosen in the interface; nil means all channels. The server injects it into
    /// every tool call. `today` is never sent by the client.
    public var provider: String?

    public init(question: String, timezone: String, provider: String?) {
        self.question = question
        self.timezone = timezone
        self.provider = provider
    }
}

/// Decodes a known string value or `.unknown`; the raw text is kept for diagnostics.
public protocol TolerantEnum: RawRepresentable, Decodable, Equatable, Sendable where RawValue == String {
    static var unknown: Self { get }
}

extension TolerantEnum {
    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .unknown
    }
}

public enum DataStatus: String, TolerantEnum {
    case answered
    case insufficientData = "insufficient_data"
    case refused
    case needsClarification = "needs_clarification"
    case noToolAnswer = "no_tool_answer"
    case toolError = "tool_error"
    case budgetExceeded = "budget_exceeded"
    case modelError = "model_error"
    case unknown
}

public enum ExplanationStatus: String, TolerantEnum {
    case verified
    case unverified
    case notApplicable = "not_applicable"
    case unknown
}

public enum RowQuality: String, TolerantEnum {
    case value
    case unusable  // records exist, no reliable value (for example overlapping intervals)
    case none  // no records
    case unknown
}

public enum CardKind: String, TolerantEnum {
    case dailyTable = "daily_table"
    case comparison
    case availability
    case unknown
}

public enum Direction: String, TolerantEnum {
    case higher, lower, unchanged, unavailable, unknown
}

public struct CardRow: Decodable, Equatable, Sendable {
    public var date: String
    public var kind: String
    public var value: String?
    public var display: String
    public var quality: RowQuality
    public var records: Int
    public var warnings: [String]

    enum CodingKeys: String, CodingKey { case date, kind, value, display, quality, records, warnings }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        kind = try c.decode(String.self, forKey: .kind)
        value = try c.decodeIfPresent(String.self, forKey: .value)
        display = try c.decode(String.self, forKey: .display)
        // Older servers without `quality` are read as unknown, never as a value.
        quality = try c.decodeIfPresent(RowQuality.self, forKey: .quality) ?? .unknown
        records = try c.decodeIfPresent(Int.self, forKey: .records) ?? 0
        warnings = try c.decodeIfPresent([String].self, forKey: .warnings) ?? []
    }
}

/// One side of a comparison card: the server's numbers, kept as text exactly as sent.
public struct ComparisonSide: Decodable, Equatable, Sendable {
    public var startDate: String?
    public var endDate: String?
    public var value: String?
    public var display: String?
    public var daysWithData: Int?

    enum CodingKeys: String, CodingKey {
        case startDate = "start_date", endDate = "end_date", value, display, daysWithData = "days_with_data"
    }
}

public struct Card: Decodable, Equatable, Sendable, Identifiable {
    public var id: String { evidenceID }
    public var evidenceID: String
    public var kind: CardKind
    public var title: String
    public var timezone: String
    public var startDate: String?
    public var endDate: String?
    public var provider: String?
    public var metric: String?
    public var unit: String?
    public var statistic: String?
    public var rows: [CardRow]
    public var baseline: ComparisonSide?
    public var current: ComparisonSide?
    public var difference: String?
    public var direction: Direction?
    public var warningText: [String]

    enum CodingKeys: String, CodingKey {
        case evidenceID = "evidence_id", kind, title, timezone, startDate = "start_date", endDate = "end_date"
        case provider, metric, unit, statistic, rows, baseline, current, difference, direction
        case warningText = "warning_text"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        evidenceID = try c.decode(String.self, forKey: .evidenceID)
        kind = try c.decode(CardKind.self, forKey: .kind)
        title = try c.decode(String.self, forKey: .title)
        timezone = try c.decode(String.self, forKey: .timezone)
        startDate = try c.decodeIfPresent(String.self, forKey: .startDate)
        endDate = try c.decodeIfPresent(String.self, forKey: .endDate)
        provider = try c.decodeIfPresent(String.self, forKey: .provider)
        metric = try c.decodeIfPresent(String.self, forKey: .metric)
        unit = try c.decodeIfPresent(String.self, forKey: .unit)
        statistic = try c.decodeIfPresent(String.self, forKey: .statistic)
        rows = try c.decodeIfPresent([CardRow].self, forKey: .rows) ?? []
        baseline = try c.decodeIfPresent(ComparisonSide.self, forKey: .baseline)
        current = try c.decodeIfPresent(ComparisonSide.self, forKey: .current)
        difference = try c.decodeIfPresent(String.self, forKey: .difference)
        direction = try c.decodeIfPresent(Direction.self, forKey: .direction)
        warningText = try c.decodeIfPresent([String].self, forKey: .warningText) ?? []
    }
}

public struct Claim: Decodable, Equatable, Sendable {
    public var type: String
    public var evidenceID: String
    public var date: String?
    enum CodingKeys: String, CodingKey { case type, evidenceID = "evidence_id", date }
}

public struct VerifiedClaim: Decodable, Equatable, Sendable {
    /// nil when the model's finding text could not be parsed.
    public var claim: Claim?
    public var valid: Bool
    public var reason: String?
    public var sentence: String?
}

public struct AskResponse: Decodable, Equatable, Sendable {
    public var status: DataStatus
    public var explanationStatus: ExplanationStatus
    public var answer: String
    public var claims: [VerifiedClaim]
    public var cards: [Card]
    public var disclaimer: String

    enum CodingKeys: String, CodingKey {
        case status, explanationStatus = "explanation_status", answer, claims, cards, disclaimer
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        status = try c.decode(DataStatus.self, forKey: .status)
        explanationStatus = try c.decode(ExplanationStatus.self, forKey: .explanationStatus)
        answer = try c.decode(String.self, forKey: .answer)
        claims = try c.decodeIfPresent([VerifiedClaim].self, forKey: .claims) ?? []
        cards = try c.decodeIfPresent([Card].self, forKey: .cards) ?? []
        disclaimer = try c.decodeIfPresent(String.self, forKey: .disclaimer) ?? ""
    }
}

/// What the screen shows for a response. Pure function of the response, so it is testable
/// without SwiftUI. The two statuses are independent: data can be present while the
/// explanation failed, and a success badge requires both to be known-good.
public struct AnswerPresentation: Equatable, Sendable {
    public enum ExplanationBadge: Equatable, Sendable {
        case verified  // every finding checked against the cards
        case unverified  // fallback text shown; cards still carry the data
        case notApplicable  // refusal, clarification or error: no explanation step
    }

    public let dataStatusLabel: String
    public let explanationBadge: ExplanationBadge
    public let showsCards: Bool

    public init(_ response: AskResponse) {
        switch response.status {
        case .answered: dataStatusLabel = "data found"
        case .insufficientData: dataStatusLabel = "no usable data in that window"
        case .refused: dataStatusLabel = "declined"
        case .needsClarification: dataStatusLabel = "choose a data source"
        case .noToolAnswer: dataStatusLabel = "no data query was made"
        case .toolError: dataStatusLabel = "data query failed"
        case .budgetExceeded: dataStatusLabel = "stopped at the request limit"
        case .modelError: dataStatusLabel = "model error"
        case .unknown: dataStatusLabel = "unrecognised status"
        }
        // Only a known `answered` + `verified` pair earns the verified badge. An unknown data
        // status with a verified explanation is still shown as unverified.
        if response.status == .answered, response.explanationStatus == .verified {
            explanationBadge = .verified
        } else if response.explanationStatus == .notApplicable, response.status != .unknown {
            explanationBadge = .notApplicable
        } else {
            explanationBadge = .unverified
        }
        showsCards = !response.cards.isEmpty
    }
}
