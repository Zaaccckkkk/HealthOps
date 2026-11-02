import Foundation
import XCTest
@testable import HealthOpsCore

func isoDate(_ text: String) -> Date { ISO8601DateFormatter().date(from: text)! }

final class MapperTests: XCTestCase {
    let mapper = Mapper()
    let uuid = UUID(uuidString: "11111111-2222-4333-8444-555555555555")!

    func sample(_ kind: RawSample.Kind, value: Double = 72, start: String = "2026-10-01T12:00:00Z",
                end: String? = nil, zone: String? = nil) -> RawSample {
        RawSample(kind: kind, uuid: uuid, value: value, start: isoDate(start),
                  end: isoDate(end ?? start), sourceName: "Synthetic Source",
                  deviceName: "Synthetic Device", timeZoneIdentifier: zone)
    }

    func testHeartRateSampleUsesExactContractFields() throws {
        let observation = try mapper.map(sample(.heartRate, value: 72))
        XCTAssertEqual(observation.provider, "healthkit")
        XCTAssertEqual(observation.sourceRecordId, "11111111-2222-4333-8444-555555555555")
        XCTAssertEqual(observation.metric, .heartRate)
        XCTAssertEqual(observation.unit, "bpm")
        XCTAssertEqual(observation.value, "72")
        XCTAssertEqual(observation.aggregation, .sample)
        XCTAssertEqual(observation.sourceDevice, "Synthetic Device · Synthetic Source")
    }

    func testMissingTimeZoneFallsBackToFixedUTCNotDeviceZone() throws {
        let observation = try mapper.map(sample(.heartRate))
        XCTAssertEqual(observation.sourceTimezone, "UTC")
        XCTAssertNotEqual(Mapper.fallbackTimeZone, TimeZone.current.identifier == "UTC" ? "x" : TimeZone.current.identifier)
        let zoned = try mapper.map(sample(.heartRate, zone: "America/New_York"))
        XCTAssertEqual(zoned.sourceTimezone, "America/New_York")
    }

    func testCumulativeKindsBecomeIntervalTotalsWithConvertedUnits() throws {
        let steps = try mapper.map(sample(.stepCount, value: 420.4, end: "2026-10-01T12:10:00Z"))
        XCTAssertEqual(steps.value, "420")  // integer count
        XCTAssertEqual(steps.aggregation, .intervalTotal)
        let exercise = try mapper.map(sample(.appleExerciseTime, value: 5, end: "2026-10-01T12:05:00Z"))
        XCTAssertEqual(exercise.value, "300")  // minutes -> seconds
        XCTAssertEqual(exercise.unit, "s")
        let energy = try mapper.map(sample(.activeEnergyBurned, value: 12.25))
        XCTAssertEqual(energy.value, "12.25")
        XCTAssertEqual(energy.unit, "kcal")
    }

    func testSleepValueIsDerivedFromIntervalOnDevice() throws {
        let sleep = try mapper.map(sample(.asleepStage, value: 999, start: "2026-10-01T03:00:00Z",
                                           end: "2026-10-01T04:30:00Z"))
        XCTAssertEqual(sleep.metric, .sleepDuration)
        XCTAssertEqual(sleep.value, "5400")
        XCTAssertEqual(sleep.aggregation, .intervalTotal)
    }

    func testHRVIsAlwaysSDNNNeverRMSSD() throws {
        let hrv = try mapper.map(sample(.heartRateVariabilitySDNN, value: 48.123456789))
        XCTAssertEqual(hrv.metric, .hrvSDNN)
        XCTAssertEqual(hrv.value, "48.123457")  // six fractional digits
        XCTAssertFalse(RawSample.Kind.allCases.contains { $0.metric == .hrvRMSSD })
    }

    func testInvalidSamplesAreRejected() {
        XCTAssertThrowsError(try mapper.map(sample(.heartRate, value: 0)))
        XCTAssertThrowsError(try mapper.map(sample(.stepCount, value: -1)))
        XCTAssertThrowsError(try mapper.map(sample(.heartRate, value: .nan)))
        XCTAssertThrowsError(try mapper.map(sample(.heartRate, start: "2026-10-01T12:00:00Z",
                                                   end: "2026-10-01T11:00:00Z")))
        XCTAssertThrowsError(try mapper.map(sample(.heartRate, zone: "Mars/Olympus")))
    }

    func testDecimalStringsHaveNoFloatingNoise() {
        XCTAssertEqual(ContractEncoding.decimalString(0.1 + 0.2), "0.3")
        XCTAssertEqual(ContractEncoding.decimalString(70), "70")
        XCTAssertEqual(ContractEncoding.decimalString(1234.1234567), "1234.123457")
        XCTAssertEqual(ContractEncoding.decimalString(0), "0")
    }

    func testEncodingIsStableAcrossRuns() throws {
        let batch = ObservationBatch(observations: [try mapper.map(sample(.heartRate))])
        let first = try ContractEncoding.encoder().encode(batch)
        let second = try ContractEncoding.encoder().encode(batch)
        XCTAssertEqual(first, second)
        let text = String(decoding: first, as: UTF8.self)
        XCTAssertTrue(text.contains("\"started_at\":\"2026-10-01T12:00:00.000Z\""), text)
        XCTAssertTrue(text.contains("\"source_record_id\""), text)
        XCTAssertFalse(text.contains("user_id"))
    }
}
