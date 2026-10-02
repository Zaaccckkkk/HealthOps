// Prints the exact batch body the adapter would send for a fixed set of synthetic samples.
// tests/test_ios_contract.py runs this and validates the output with the Python contract.
import Foundation
import HealthOpsCore

func date(_ text: String) -> Date {
    ISO8601DateFormatter().date(from: text)!
}

func id(_ n: Int) -> UUID {
    UUID(uuidString: String(format: "00000000-0000-4000-8000-%012d", n))!
}

let samples = [
    RawSample(kind: .heartRate, uuid: id(1), value: 72, start: date("2026-10-01T12:00:00Z"),
              end: date("2026-10-01T12:00:00Z"), sourceName: "Synthetic Source",
              deviceName: "Synthetic Device"),
    RawSample(kind: .restingHeartRate, uuid: id(2), value: 55.5, start: date("2026-10-01T04:00:00Z"),
              end: date("2026-10-01T04:00:00Z"), sourceName: "Synthetic Source",
              timeZoneIdentifier: "America/New_York"),
    RawSample(kind: .heartRateVariabilitySDNN, uuid: id(3), value: 48.123456789,
              start: date("2026-10-01T06:30:00Z"), end: date("2026-10-01T06:30:00Z"),
              sourceName: "Synthetic Source"),
    RawSample(kind: .stepCount, uuid: id(4), value: 420, start: date("2026-10-01T13:00:00Z"),
              end: date("2026-10-01T13:10:00Z"), sourceName: "Synthetic Source"),
    RawSample(kind: .activeEnergyBurned, uuid: id(5), value: 12.25, start: date("2026-10-01T13:00:00Z"),
              end: date("2026-10-01T13:10:00Z"), sourceName: "Synthetic Source"),
    RawSample(kind: .appleExerciseTime, uuid: id(6), value: 5, start: date("2026-10-01T13:00:00Z"),
              end: date("2026-10-01T13:05:00Z"), sourceName: "Synthetic Source"),
    RawSample(kind: .asleepStage, uuid: id(7), value: 0, start: date("2026-10-01T03:00:00Z"),
              end: date("2026-10-01T04:30:00Z"), sourceName: "Synthetic Source"),
]

let mapper = Mapper()
let batch = ObservationBatch(observations: try samples.map(mapper.map))
let encoder = ContractEncoding.encoder()
encoder.outputFormatting.insert(.prettyPrinted)
let data = try encoder.encode(batch)
print(String(decoding: data, as: UTF8.self))
