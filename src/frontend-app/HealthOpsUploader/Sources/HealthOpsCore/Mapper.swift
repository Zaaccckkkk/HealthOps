import Foundation

public enum MappingError: Error, Equatable {
    case nonFiniteValue(UUID)
    case negativeValue(UUID)
    case endBeforeStart(UUID)
    case zeroHeartRate(UUID)
    case invalidTimeZone(UUID, String)
}

/// Maps raw samples to the canonical contract. Pure and deterministic: the same sample
/// always yields the same observation, so a persisted payload can be replayed safely.
public struct Mapper: Sendable {
    public static let provider = "healthkit"
    /// Used when a sample carries no time-zone metadata. Fixed on purpose: the device's current
    /// zone changes with travel and would turn a replay into a 409 conflict. UTC here means
    /// "unknown collection zone", not that the user was in UTC.
    public static let fallbackTimeZone = "UTC"

    public init() {}

    public func map(_ sample: RawSample) throws -> CanonicalObservation {
        guard sample.end >= sample.start else { throw MappingError.endBeforeStart(sample.uuid) }
        let value: Double
        switch sample.kind {
        case .asleepStage:
            // HealthKit sleep samples have no quantity; the asleep duration is the interval.
            // This is the adapter's explicit conversion, documented in the contract.
            value = sample.end.timeIntervalSince(sample.start)
        case .appleExerciseTime:
            value = sample.value * 60  // requested in minutes, contract unit is seconds
        case .stepCount:
            value = sample.value.rounded()
        default:
            value = sample.value
        }
        guard value.isFinite else { throw MappingError.nonFiniteValue(sample.uuid) }
        guard value >= 0 else { throw MappingError.negativeValue(sample.uuid) }
        if (sample.kind == .heartRate || sample.kind == .restingHeartRate) && value == 0 {
            throw MappingError.zeroHeartRate(sample.uuid)
        }
        let zone = sample.timeZoneIdentifier ?? Self.fallbackTimeZone
        guard TimeZone(identifier: zone) != nil else {
            throw MappingError.invalidTimeZone(sample.uuid, zone)
        }
        let metric = sample.kind.metric
        return CanonicalObservation(
            provider: Self.provider,
            sourceRecordId: sample.uuid.uuidString.lowercased(),
            sourceDevice: Self.deviceLabel(sample),
            metric: metric,
            value: ContractEncoding.decimalString(value),
            unit: metric.unit,
            startedAt: sample.start,
            endedAt: sample.end,
            sourceTimezone: zone,
            aggregation: sample.kind == .asleepStage || Self.isCumulative(sample.kind)
                ? .intervalTotal : .sample
        )
    }

    static func isCumulative(_ kind: RawSample.Kind) -> Bool {
        switch kind {
        case .stepCount, .activeEnergyBurned, .appleExerciseTime: return true
        default: return false
        }
    }

    /// Provenance label, never a claim of verified hardware: "Device · Source" or "Source".
    static func deviceLabel(_ sample: RawSample) -> String? {
        let source = sample.sourceName.trimmingCharacters(in: .whitespaces)
        if let device = sample.deviceName?.trimmingCharacters(in: .whitespaces),
           !device.isEmpty, device != source {
            return String("\(device) · \(source)".prefix(150))
        }
        return source.isEmpty ? nil : String(source.prefix(150))
    }
}
