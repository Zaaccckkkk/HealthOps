import Foundation

/// What the adapter reads from HealthKit, expressed without HealthKit types so the mapping
/// can be tested on macOS. `value` is already in the unit named by `kind.requestedUnit`.
public struct RawSample: Equatable, Sendable {
    public enum Kind: String, CaseIterable, Sendable {
        case heartRate
        case restingHeartRate
        case heartRateVariabilitySDNN
        case stepCount
        case activeEnergyBurned
        case appleExerciseTime
        /// A sleepAnalysis category sample in an asleep state (unspecified, core, deep, REM).
        /// in-bed and awake samples must not be passed to the mapper.
        case asleepStage

        /// The explicit HKUnit the reader must request. Display units are never used.
        public var requestedUnit: String {
            switch self {
            case .heartRate, .restingHeartRate: return "count/min"
            case .heartRateVariabilitySDNN: return "ms"
            case .stepCount: return "count"
            case .activeEnergyBurned: return "kcal"
            case .appleExerciseTime: return "min"
            case .asleepStage: return "s (derived from the sample interval)"
            }
        }

        public var metric: Metric {
            switch self {
            case .heartRate: return .heartRate
            case .restingHeartRate: return .restingHeartRate
            case .heartRateVariabilitySDNN: return .hrvSDNN
            case .stepCount: return .steps
            case .activeEnergyBurned: return .activeEnergy
            case .appleExerciseTime: return .exerciseDuration
            case .asleepStage: return .sleepDuration
            }
        }
    }

    public var kind: Kind
    /// `HKSample.uuid`: stable across reads, so replays are recognised by the server.
    public var uuid: UUID
    /// Numeric value in `kind.requestedUnit`; ignored for `.asleepStage`.
    public var value: Double
    public var start: Date
    public var end: Date
    /// `HKSourceRevision.source.name`: the app that wrote the sample, not a verified device.
    public var sourceName: String
    /// `HKDevice.name` or `.model` when HealthKit provides one.
    public var deviceName: String?
    /// `HKMetadataKeyTimeZone` when the writer recorded it; most samples do not carry it.
    public var timeZoneIdentifier: String?

    public init(
        kind: Kind, uuid: UUID, value: Double, start: Date, end: Date, sourceName: String,
        deviceName: String? = nil, timeZoneIdentifier: String? = nil
    ) {
        self.kind = kind
        self.uuid = uuid
        self.value = value
        self.start = start
        self.end = end
        self.sourceName = sourceName
        self.deviceName = deviceName
        self.timeZoneIdentifier = timeZoneIdentifier
    }
}
