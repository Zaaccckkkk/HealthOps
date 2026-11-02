import Foundation
import HealthOpsCore
import SwiftUI

/// Loads deterministic per-day values for the home charts straight from the tool route. No
/// model is involved: every number on the screen is a server-computed statistic for one local
/// day, and days without a reliable value are shown as gaps.
@MainActor
final class HomeModel: ObservableObject {
    enum Range: Int, CaseIterable, Identifiable {
        case week = 7, month = 30
        var id: Int { rawValue }
        var label: String { self == .week ? "7 days" : "30 days" }
    }

    // SDNN (Apple Health) and RMSSD (Google Health) are different HRV definitions and get
    // separate charts; they are never drawn on one axis.
    static let metrics: [Metric] = [
        .steps, .restingHeartRate, .heartRate, .hrvSDNN, .hrvRMSSD, .sleepDuration, .activeEnergy, .exerciseDuration,
    ]

    @Published var range: Range = .week
    @Published private(set) var series: [Metric: [MetricSeries]] = [:]
    @Published private(set) var failures: [Metric: String] = [:]
    @Published private(set) var loading = false
    @Published private(set) var loadedAt: Date?

    private var task: Task<Void, Never>?
    private var generation = 0

    func reset() {
        task?.cancel()
        task = nil
        generation += 1
        series = [:]
        failures = [:]
        loading = false
        loadedAt = nil
    }

    func load(serverURL: URL, token: String, provider: String?) {
        task?.cancel()
        generation += 1
        let myGeneration = generation
        let client = APIClient(baseURL: serverURL, token: token)
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let start = calendar.date(byAdding: .day, value: -(range.rawValue - 1), to: today)!
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "yyyy-MM-dd"
        let (startText, endText) = (formatter.string(from: start), formatter.string(from: today))
        let timezone = TimeZone.current.identifier
        loading = true
        task = Task { [weak self] in
            var loaded: [Metric: [MetricSeries]] = [:]
            var failed: [Metric: String] = [:]
            for metric in Self.metrics {
                if Task.isCancelled { return }
                do {
                    let response = try await client.dailySummary(
                        metric: metric, startDate: startText, endDate: endText, timezone: timezone,
                        provider: provider)
                    loaded[metric] = MetricSeries.from(response)
                } catch let failure as ServiceFailure {
                    failed[metric] = failure.message
                } catch {
                    failed[metric] = (error as NSError).localizedDescription
                }
            }
            await MainActor.run {
                guard let self, self.generation == myGeneration else { return }  // stale after reset
                self.series = loaded
                self.failures = failed
                self.loading = false
                self.loadedAt = Date()
            }
        }
    }
}
