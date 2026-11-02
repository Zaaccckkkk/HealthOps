import Foundation

// Swift mirror of the deterministic daily-summary tool result (GET /v1/tools/daily-summary),
// reduced to what a chart needs. The per-day value follows the server's own rule (the same one
// the assistant's cards use): point metrics show the sample mean, cumulative metrics the daily
// total or, failing that, the sum of recorded intervals. Days with records but no reliable
// value stay distinct from days without records; nothing is interpolated or summed here.

public struct DailySummaryResponse: Decodable, Equatable, Sendable {
    public struct Window: Decodable, Equatable, Sendable {
        public var timezone: String
        public var startDate: String
        public var endDate: String
        enum CodingKeys: String, CodingKey { case timezone, startDate = "start_date", endDate = "end_date" }
    }

    public struct Day: Decodable, Equatable, Sendable {
        public var date: String
        public var sampleCount: Int
        public var sampleMean: String?
        public var intervalTotalCount: Int
        public var intervalTotalSum: String?
        public var dailyTotalCount: Int
        public var dailyTotal: String?
        public var dailySummaryCount: Int
        public var dailySummary: String?
        public var unalignedCount: Int
        public var unsupportedCount: Int
        public var warnings: [String]

        enum CodingKeys: String, CodingKey {
            case date, warnings
            case sampleCount = "sample_count", sampleMean = "sample_mean"
            case intervalTotalCount = "interval_total_count", intervalTotalSum = "interval_total_sum"
            case dailyTotalCount = "daily_total_count", dailyTotal = "daily_total"
            case dailySummaryCount = "daily_summary_count", dailySummary = "daily_summary"
            case unalignedCount = "unaligned_count", unsupportedCount = "unsupported_count"
        }

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            date = try c.decode(String.self, forKey: .date)
            sampleCount = try c.decodeIfPresent(Int.self, forKey: .sampleCount) ?? 0
            sampleMean = try c.decodeIfPresent(String.self, forKey: .sampleMean)
            intervalTotalCount = try c.decodeIfPresent(Int.self, forKey: .intervalTotalCount) ?? 0
            intervalTotalSum = try c.decodeIfPresent(String.self, forKey: .intervalTotalSum)
            dailyTotalCount = try c.decodeIfPresent(Int.self, forKey: .dailyTotalCount) ?? 0
            dailyTotal = try c.decodeIfPresent(String.self, forKey: .dailyTotal)
            dailySummaryCount = try c.decodeIfPresent(Int.self, forKey: .dailySummaryCount) ?? 0
            dailySummary = try c.decodeIfPresent(String.self, forKey: .dailySummary)
            unalignedCount = try c.decodeIfPresent(Int.self, forKey: .unalignedCount) ?? 0
            unsupportedCount = try c.decodeIfPresent(Int.self, forKey: .unsupportedCount) ?? 0
            warnings = try c.decodeIfPresent([String].self, forKey: .warnings) ?? []
        }

        var records: Int {
            sampleCount + intervalTotalCount + dailyTotalCount + dailySummaryCount + unalignedCount + unsupportedCount
        }
    }

    public struct ProviderDays: Decodable, Equatable, Sendable {
        public var provider: String
        public var recordCount: Int
        public var days: [Day]
        enum CodingKeys: String, CodingKey { case provider, recordCount = "record_count", days }
    }

    public var metric: String
    public var unit: String
    public var metricClass: String
    public var window: Window
    public var recordCount: Int
    public var providers: [ProviderDays]
    public var warnings: [String]

    enum CodingKeys: String, CodingKey {
        case metric, unit, window, providers, warnings
        case metricClass = "metric_class", recordCount = "record_count"
    }
}

/// One day on a chart. `value` is the server's number parsed for plotting; `text` is the
/// server's decimal string, kept for display so nothing is re-rounded.
public struct DailyPoint: Equatable, Sendable, Identifiable {
    public var id: String { date }
    public let date: String
    public let value: Double?
    public let text: String?
    public let statistic: String
    public let quality: RowQuality
    public let records: Int
    public let warnings: [String]
}

public struct MetricSeries: Equatable, Sendable {
    public let metric: String
    public let unit: String
    public let provider: String
    public let statistic: String
    public let points: [DailyPoint]

    /// Builds one series per provider from a tool result, applying the server's value rule.
    public static func from(_ response: DailySummaryResponse) -> [MetricSeries] {
        response.providers.map { provider in
            let points = provider.days.map { day -> DailyPoint in
                let (text, statistic): (String?, String)
                if response.metricClass == "point" {
                    if let mean = day.sampleMean { (text, statistic) = (mean, "sample_mean") }
                    else { (text, statistic) = (day.dailySummary, "daily_summary") }
                } else if let total = day.dailyTotal {
                    (text, statistic) = (total, "daily_total")
                } else {
                    (text, statistic) = (day.intervalTotalSum, "interval_total_sum")
                }
                let quality: RowQuality = text != nil ? .value : (day.records > 0 ? .unusable : .none)
                return DailyPoint(
                    date: day.date, value: text.flatMap(Double.init), text: text,
                    statistic: text == nil ? "none" : statistic, quality: quality,
                    records: day.records, warnings: day.warnings)
            }
            let statistic = points.first(where: { $0.quality == .value })?.statistic ?? "none"
            return MetricSeries(
                metric: response.metric, unit: response.unit, provider: provider.provider,
                statistic: statistic, points: points)
        }
    }

    /// Consecutive runs of days with values. A line is drawn within a run only, so a gap in
    /// the data is shown as a gap, never bridged.
    public var segments: [[DailyPoint]] {
        var result: [[DailyPoint]] = []
        var current: [DailyPoint] = []
        for point in points {
            if point.quality == .value {
                current.append(point)
            } else if !current.isEmpty {
                result.append(current)
                current = []
            }
        }
        if !current.isEmpty { result.append(current) }
        return result
    }

    public var daysWithValues: Int { points.filter { $0.quality == .value }.count }
    public var daysUnusable: Int { points.filter { $0.quality == .unusable }.count }
    public var daysWithoutRecords: Int { points.filter { $0.quality == .none }.count }
    public var latest: DailyPoint? { points.last(where: { $0.quality == .value }) }

    /// Display text mirroring the server's `format_value`: seconds as hours and minutes,
    /// counts as integers, otherwise one decimal (half-even). Only for presentation.
    public static func display(_ text: String?, unit: String) -> String {
        guard let text, let number = Decimal(string: text) else { return "no data" }
        if unit == "s" {
            let total = NSDecimalNumber(decimal: number).intValue
            return "\(total / 3600) h \((total % 3600) / 60) min"
        }
        if unit == "count" { return "\(NSDecimalNumber(decimal: number).intValue) \(unit)" }
        var rounded = Decimal()
        var source = number
        NSDecimalRound(&rounded, &source, 1, .bankers)
        if rounded == Decimal(NSDecimalNumber(decimal: rounded).intValue) {
            return "\(NSDecimalNumber(decimal: rounded).intValue) \(unit)"
        }
        return "\(rounded) \(unit)"
    }
}
