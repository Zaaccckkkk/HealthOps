import Foundation
import XCTest
@testable import HealthOpsCore

final class DailySeriesTests: XCTestCase {
    func load() throws -> DailySummaryResponse {
        let url = Bundle.module.url(forResource: "daily-summary.synthetic", withExtension: "json", subdirectory: "Fixtures/tools")
        return try JSONDecoder().decode(DailySummaryResponse.self, from: Data(contentsOf: XCTUnwrap(url)))
    }

    func testCommittedToolExampleDecodesAndFollowsTheServerValueRule() throws {
        let response = try load()
        XCTAssertEqual(response.metric, "steps")
        let series = MetricSeries.from(response)
        XCTAssertFalse(series.isEmpty)
        // Cumulative metric: the daily total wins over the interval sum when both exist.
        let first = series[0].points[0]
        XCTAssertEqual(first.statistic, "daily_total")
        XCTAssertEqual(first.text, "6400")
        XCTAssertEqual(first.quality, .value)
        XCTAssertEqual(first.warnings, ["mixed_total_kinds"])
    }

    func testSegmentsBreakAtDaysWithoutAValue() {
        func day(_ d: String, _ v: String?, records: Int) -> DailyPoint {
            DailyPoint(date: d, value: v.flatMap(Double.init), text: v, statistic: v == nil ? "none" : "sample_mean",
                       quality: v != nil ? .value : (records > 0 ? .unusable : .none), records: records, warnings: [])
        }
        let series = MetricSeries(metric: "heart_rate", unit: "bpm", provider: "healthkit", statistic: "sample_mean", points: [
            day("2026-10-01", "60", records: 1), day("2026-10-02", "61", records: 1),
            day("2026-10-03", nil, records: 0), day("2026-10-04", "63", records: 1),
            day("2026-10-05", nil, records: 2), day("2026-10-06", "0", records: 1),
        ])
        XCTAssertEqual(series.segments.map { $0.map(\.date) }, [["2026-10-01", "2026-10-02"], ["2026-10-04"], ["2026-10-06"]])
        XCTAssertEqual(series.daysWithValues, 4)
        XCTAssertEqual(series.daysWithoutRecords, 1)
        XCTAssertEqual(series.daysUnusable, 1)
        XCTAssertEqual(series.latest?.text, "0")  // zero is a value, not missing data
    }

    func testDisplayMirrorsTheServerFormatting() {
        XCTAssertEqual(MetricSeries.display("24300.000000", unit: "s"), "6 h 45 min")
        XCTAssertEqual(MetricSeries.display("6400", unit: "count"), "6400 count")
        XCTAssertEqual(MetricSeries.display("57.75", unit: "bpm"), "57.8 bpm")
        XCTAssertEqual(MetricSeries.display("56.83", unit: "bpm"), "56.8 bpm")
        XCTAssertEqual(MetricSeries.display("62.000000", unit: "bpm"), "62 bpm")
        XCTAssertEqual(MetricSeries.display(nil, unit: "bpm"), "no data")
    }
}
