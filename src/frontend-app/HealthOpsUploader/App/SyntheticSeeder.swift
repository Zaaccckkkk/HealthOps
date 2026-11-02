import Foundation
import HealthKit

#if DEBUG && targetEnvironment(simulator)
/// Writes clearly labelled synthetic samples into the Simulator's HealthKit store so the upload
/// flow can be demonstrated without any real health data. Compiled only for debug Simulator
/// builds; the source name HealthKit records is this app, never a watch.
enum SyntheticSeeder {
    static let metadata: [String: Any] = ["HealthOpsSynthetic": true]

    /// Types the Simulator seeder writes. appleExerciseTime is read-only for third-party apps
    /// (requesting share authorization for it raises an exception), so exercise_duration has
    /// no HealthKit end-to-end path here; its mapping is covered by unit tests only.
    static func writeTypes() -> Set<HKSampleType> {
        Set(SyncedType.allCases.filter { $0 != .exerciseTime }.map(\.sampleType))
    }

    /// Returns the number of samples written. Each call produces new UUIDs, so repeated seeding
    /// adds records; the server treats them as new source records.
    static func seed(into store: HKHealthStore, around reference: Date, days: Int = 1) async throws -> Int {
        var total = 0
        // Day 3 before the reference gets no samples at all, so the charts show a real gap.
        for back in 0..<days where back != 3 {
            let day = Calendar(identifier: .gregorian).date(byAdding: .day, value: -back, to: reference)!
            total += try await seedDay(into: store, reference: day, variation: Double(back % 5))
        }
        return total
    }

    private static func seedDay(into store: HKHealthStore, reference: Date, variation: Double) async throws -> Int {
        let calendar = Calendar(identifier: .gregorian)
        let dayStart = calendar.startOfDay(for: reference)
        func at(_ hour: Int, _ minute: Int = 0) -> Date {
            calendar.date(byAdding: .minute, value: hour * 60 + minute, to: dayStart)!
        }
        var samples: [HKSample] = []
        let bpm = HKUnit.count().unitDivided(by: .minute())
        for (offset, value) in [(8, 62.0 + variation), (12, 71.0 + variation), (18, 84.0 - variation)] {
            samples.append(HKQuantitySample(
                type: HKQuantityType(.heartRate), quantity: HKQuantity(unit: bpm, doubleValue: value),
                start: at(offset), end: at(offset), metadata: metadata))
        }
        samples.append(HKQuantitySample(
            type: HKQuantityType(.restingHeartRate), quantity: HKQuantity(unit: bpm, doubleValue: 55 + variation),
            start: at(6), end: at(6), metadata: metadata))
        samples.append(HKQuantitySample(
            type: HKQuantityType(.heartRateVariabilitySDNN),
            quantity: HKQuantity(unit: .secondUnit(with: .milli), doubleValue: 48.5 - variation),
            start: at(6, 30), end: at(6, 30), metadata: metadata))
        for (hour, steps) in [(9, 1200.0 + 300 * variation), (13, 800.0), (17, 2500.0 - 200 * variation)] {
            samples.append(HKQuantitySample(
                type: HKQuantityType(.stepCount), quantity: HKQuantity(unit: .count(), doubleValue: steps),
                start: at(hour), end: at(hour, 30), metadata: metadata))
        }
        samples.append(HKQuantitySample(
            type: HKQuantityType(.activeEnergyBurned),
            quantity: HKQuantity(unit: .kilocalorie(), doubleValue: 35.5 + 4 * variation),
            start: at(17), end: at(17, 30), metadata: metadata))
        // Last night: 23:00 to 06:30 as one core and one REM stage, no overlap.
        samples.append(HKCategorySample(
            type: HKCategoryType(.sleepAnalysis), value: HKCategoryValueSleepAnalysis.asleepCore.rawValue,
            start: at(-1), end: at(4), metadata: metadata))
        samples.append(HKCategorySample(
            type: HKCategoryType(.sleepAnalysis), value: HKCategoryValueSleepAnalysis.asleepREM.rawValue,
            start: at(4), end: at(6, 30), metadata: metadata))
        try await store.save(samples)
        return samples.count
    }
}
#endif
