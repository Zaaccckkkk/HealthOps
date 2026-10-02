import Foundation

/// Canonical metric names accepted by `POST /v1/observations/batch`.
public enum Metric: String, Codable, CaseIterable, Sendable {
    case heartRate = "heart_rate"
    case restingHeartRate = "resting_heart_rate"
    case steps = "steps"
    case hrvSDNN = "hrv_sdnn"
    case hrvRMSSD = "hrv_rmssd"
    case sleepDuration = "sleep_duration"
    case activeEnergy = "active_energy"
    case exerciseDuration = "exercise_duration"

    /// The server validates this pair exactly; the adapter converts before sending.
    public var unit: String {
        switch self {
        case .heartRate, .restingHeartRate: return "bpm"
        case .steps: return "count"
        case .hrvSDNN, .hrvRMSSD: return "ms"
        case .sleepDuration, .exerciseDuration: return "s"
        case .activeEnergy: return "kcal"
        }
    }
}

public enum Aggregation: String, Codable, Sendable {
    case sample
    case intervalTotal = "interval_total"
    case dailyTotal = "daily_total"
    case dailySummary = "daily_summary"
}

/// One observation exactly as the server contract defines it. Field names match the JSON.
/// `value` is a decimal string with at most six fractional digits; never a display string.
public struct CanonicalObservation: Codable, Equatable, Sendable {
    public var provider: String
    public var sourceRecordId: String
    public var sourceDevice: String?
    public var metric: Metric
    public var value: String
    public var unit: String
    public var startedAt: Date
    public var endedAt: Date
    public var sourceTimezone: String
    public var aggregation: Aggregation

    enum CodingKeys: String, CodingKey {
        case provider
        case sourceRecordId = "source_record_id"
        case sourceDevice = "source_device"
        case metric, value, unit
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case sourceTimezone = "source_timezone"
        case aggregation
    }
}

public struct ObservationBatch: Codable, Equatable, Sendable {
    public var observations: [CanonicalObservation]
    public init(observations: [CanonicalObservation]) { self.observations = observations }
}

public struct BatchResult: Codable, Equatable, Sendable {
    public var received: Int
    public var inserted: Int
    public var duplicates: Int
    public init(received: Int, inserted: Int, duplicates: Int) {
        self.received = received
        self.inserted = inserted
        self.duplicates = duplicates
    }
}

public enum ContractEncoding {
    /// Deterministic encoder: UTC instants with milliseconds, sorted keys, stable bytes.
    /// The same observations always produce the same body, which the server's replay
    /// detection relies on.
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        encoder.dateEncodingStrategy = .custom { date, container in
            var single = container.singleValueContainer()
            try single.encode(Self.timestamp(date))
        }
        return encoder
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            if let date = formatter.date(from: text) ?? plainFormatter.date(from: text) {
                return date
            }
            throw DecodingError.dataCorrupted(
                .init(codingPath: decoder.codingPath, debugDescription: "Expected ISO 8601: \(text)"))
        }
        return decoder
    }

    private static let plainFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    private static let formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        formatter.timeZone = TimeZone(identifier: "UTC")
        return formatter
    }()

    public static func timestamp(_ date: Date) -> String {
        formatter.string(from: date)
    }

    /// Decimal text with at most six fractional digits and no trailing zeros, e.g. "72",
    /// "0.5", "1234.123457". Non-finite values are rejected by the mapper before this point.
    public static func decimalString(_ value: Double) -> String {
        var text = String(format: "%.6f", value)
        while text.hasSuffix("0") { text.removeLast() }
        if text.hasSuffix(".") { text.removeLast() }
        return text == "-0" ? "0" : text
    }
}
