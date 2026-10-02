import Foundation
import HealthKit
import HealthOpsCore

/// The HealthKit types this adapter reads, with the explicit unit requested for each.
enum SyncedType: CaseIterable {
    case heartRate, restingHeartRate, hrvSDNN, steps, activeEnergy, exerciseTime, sleep

    var kind: RawSample.Kind {
        switch self {
        case .heartRate: return .heartRate
        case .restingHeartRate: return .restingHeartRate
        case .hrvSDNN: return .heartRateVariabilitySDNN
        case .steps: return .stepCount
        case .activeEnergy: return .activeEnergyBurned
        case .exerciseTime: return .appleExerciseTime
        case .sleep: return .asleepStage
        }
    }

    var sampleType: HKSampleType {
        switch self {
        case .heartRate: return HKQuantityType(.heartRate)
        case .restingHeartRate: return HKQuantityType(.restingHeartRate)
        case .hrvSDNN: return HKQuantityType(.heartRateVariabilitySDNN)
        case .steps: return HKQuantityType(.stepCount)
        case .activeEnergy: return HKQuantityType(.activeEnergyBurned)
        case .exerciseTime: return HKQuantityType(.appleExerciseTime)
        case .sleep: return HKCategoryType(.sleepAnalysis)
        }
    }

    /// Explicit HKUnit; display formatting is never involved.
    var unit: HKUnit? {
        switch self {
        case .heartRate, .restingHeartRate: return HKUnit.count().unitDivided(by: .minute())
        case .hrvSDNN: return .secondUnit(with: .milli)
        case .steps: return .count()
        case .activeEnergy: return .kilocalorie()
        case .exerciseTime: return .minute()
        case .sleep: return nil
        }
    }

    var identifier: String { sampleType.identifier }
    var metric: Metric { kind.metric }
}

/// HealthKit-backed page source: `HKAnchoredObjectQuery` with a page limit. The anchor it
/// returns is only persisted as a *candidate*; `SyncEngine` commits it after upload.
final class HealthKitSampleSource: SampleSource, @unchecked Sendable {
    private let store: HKHealthStore
    private let type: SyncedType
    private let notBefore: Date

    init(store: HKHealthStore, type: SyncedType, notBefore: Date) {
        self.store = store
        self.type = type
        self.notBefore = notBefore
    }

    func fetchPage(after anchor: Data?, limit: Int) async throws -> SamplePage {
        let decodedAnchor = try anchor.flatMap {
            try NSKeyedUnarchiver.unarchivedObject(ofClass: HKQueryAnchor.self, from: $0)
        }
        // The consented history start is "earliest sample START"; strictStartDate excludes samples
        // that merely overlap it (for example a sleep interval that began the night before).
        let predicate = HKQuery.predicateForSamples(
            withStart: notBefore, end: nil, options: .strictStartDate)
        return try await withCheckedThrowingContinuation { continuation in
            let query = HKAnchoredObjectQuery(
                type: type.sampleType, predicate: predicate, anchor: decodedAnchor, limit: limit
            ) { [type] _, samples, deleted, newAnchor, error in
                if let error {
                    continuation.resume(throwing: error)
                    return
                }
                let returned = samples ?? []
                let raw = returned.compactMap { Self.rawSample($0, type: type) }
                let anchorData = newAnchor.flatMap {
                    try? NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true)
                }
                // HealthKit's `limit` counts samples and deleted objects together, so the paging
                // signal must too: a page consisting only of deletions is still a full page.
                let deletedCount = deleted?.count ?? 0
                continuation.resume(returning: SamplePage(
                    samples: raw, queryReturnedCount: returned.count + deletedCount,
                    deletedCount: deletedCount, anchor: anchorData))
            }
            store.execute(query)
        }
    }

    static func rawSample(_ sample: HKSample, type: SyncedType) -> RawSample? {
        let zone = sample.metadata?[HKMetadataKeyTimeZone] as? String
        let device = sample.device?.name ?? sample.device?.model
        let source = sample.sourceRevision.source.name
        switch sample {
        case let quantity as HKQuantitySample:
            guard let unit = type.unit else { return nil }
            return RawSample(
                kind: type.kind, uuid: sample.uuid, value: quantity.quantity.doubleValue(for: unit),
                start: sample.startDate, end: sample.endDate, sourceName: source,
                deviceName: device, timeZoneIdentifier: zone)
        case let category as HKCategorySample where type == .sleep:
            // Only asleep states are uploaded; in-bed and awake are not sleep duration.
            let asleep: Set<Int> = [
                HKCategoryValueSleepAnalysis.asleepUnspecified.rawValue,
                HKCategoryValueSleepAnalysis.asleepCore.rawValue,
                HKCategoryValueSleepAnalysis.asleepDeep.rawValue,
                HKCategoryValueSleepAnalysis.asleepREM.rawValue,
            ]
            guard asleep.contains(category.value) else { return nil }
            return RawSample(
                kind: .asleepStage, uuid: sample.uuid, value: 0, start: sample.startDate,
                end: sample.endDate, sourceName: source, deviceName: device, timeZoneIdentifier: zone)
        default:
            return nil
        }
    }
}
