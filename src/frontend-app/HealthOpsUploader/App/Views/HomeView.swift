import Charts
import HealthOpsCore
import SwiftUI

/// User-facing overview: one chart per metric from the deterministic daily-summary tool.
/// Before the account is connected it shows the setup steps instead.
struct HomeView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var home: HomeModel
    @Binding var selectedTab: ContentView.Tab

    var body: some View {
        NavigationStack {
            Group {
                // A stored token and a server address are enough to read charts; a rejected token
                // or an unreachable server shows up as a failure card, not as a blank screen.
                if !model.hasToken || model.serverURL == nil {
                    SetupStepsView(selectedTab: $selectedTab)
                } else {
                    charts
                }
            }
            .navigationTitle("Overview")
            .toolbar {
                if model.hasToken {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { reload() } label: { Image(systemName: "arrow.clockwise") }
                            .disabled(home.loading)
                    }
                }
            }
            .onAppear { if home.loadedAt == nil { reload() } }
            .onChange(of: home.range) { _, _ in reload() }
        }
    }

    private func reload() {
        guard let url = model.serverURL, let token = model.currentToken() else { return }
        home.load(serverURL: url, token: token, provider: nil)
    }

    private var charts: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Picker("Range", selection: $home.range) {
                    ForEach(HomeModel.Range.allCases) { range in Text(range.label).tag(range) }
                }
                .pickerStyle(.segmented)
                if model.user == nil {
                    Text("Account not verified yet: charts use the stored token; connect on the Setup tab to confirm who you are.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                if home.loading && home.series.isEmpty {
                    ProgressView("Loading daily values…").frame(maxWidth: .infinity)
                }
                ForEach(HomeModel.metrics, id: \.self) { metric in
                    if let failure = home.failures[metric] {
                        MetricFailureCard(metric: metric, message: failure)
                    } else if let series = home.series[metric] {
                        if series.isEmpty {
                            MetricEmptyCard(metric: metric, range: home.range)
                        } else {
                            MetricChartCard(metric: metric, channels: series)
                        }
                    }
                }
                if let loadedAt = home.loadedAt {
                    Text("Values are per local day (\(TimeZone.current.identifier)), computed by the server. Loaded \(loadedAt.formatted(date: .omitted, time: .shortened)). Not medical advice.")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .padding()
        }
        .refreshable { reload() }
    }
}

struct SetupStepsView: View {
    @EnvironmentObject private var model: AppModel
    @Binding var selectedTab: ContentView.Tab

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Your recorded health history, explained from the data.")
                    .font(.title3.bold())
                Text("HealthOps shows per-day values computed by your own server and answers questions only from those values. Three steps before the first chart:")
                    .font(.callout)
                step(1, "Connect", "Enter the server address and your token, then verify the account.", done: model.user != nil)
                step(2, "Allow reading Health data", "iOS asks which types this app may read on the phone.", done: model.healthKitRequested)
                step(3, "Choose what to upload", "Pick the metrics and the start date; nothing leaves the phone before this.", done: model.consentCoversCurrentScope)
                Button("Open setup") { selectedTab = .setup }
                    .buttonStyle(.borderedProminent)
            }
            .padding()
        }
    }

    private func step(_ number: Int, _ title: String, _ text: String, done: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: done ? "checkmark.circle.fill" : "\(number).circle")
                .foregroundStyle(done ? Color.green : Color.accentColor)
                .font(.title3)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.bold())
                Text(text).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// One chart per metric; one line per data channel (Apple Health, Google Health), each in
/// its own colour with a legend. Channels are never summed: two lines mean two sources.
struct MetricChartCard: View {
    let metric: Metric
    let channels: [MetricSeries]

    private var title: String { metric.rawValue.replacingOccurrences(of: "_", with: " ").capitalized }
    private var unit: String { channels.first?.unit ?? "" }
    private var plotsHours: Bool { unit == "s" }
    private var axisUnit: String { plotsHours ? "h" : unit }
    private func plotted(_ value: Double?) -> Double { plotsHours ? (value ?? 0) / 3600 : (value ?? 0) }
    /// The union of dates across channels, in calendar order (all channels share the window).
    private var dates: [String] { Array(Set(channels.flatMap { $0.points.map(\.date) })).sorted() }
    private var labelledDates: [String] {
        let step = max(1, dates.count / 6)
        return dates.enumerated().filter { $0.offset % step == 0 || $0.offset == dates.count - 1 }.map(\.element)
    }
    private var anyValues: Bool { channels.contains { $0.daysWithValues > 0 } }

    private var plotPoints: [ChartPoint] {
        channels.flatMap { series in
            series.segments.enumerated().flatMap { index, segment in
                segment.map { point in
                    ChartPoint(
                        id: "\(series.provider)-\(point.date)", date: point.date,
                        y: plotted(point.value), run: "\(series.provider)-\(index)",
                        color: color(for: series.provider))
                }
            }
        }
    }

    private var gapMarks: [ChartGap] {
        channels.flatMap { series in
            series.points.filter { $0.quality != .value }.map { point in
                ChartGap(
                    id: "\(series.provider)-\(point.date)", date: point.date,
                    color: (point.quality == .unusable ? Color.orange : color(for: series.provider)).opacity(0.3))
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.subheadline.bold())
            ForEach(channels, id: \.provider) { series in
                HStack(spacing: 6) {
                    Circle().fill(color(for: series.provider)).frame(width: 8, height: 8)
                    Text(ProviderLabel.display(series.provider)).font(.caption)
                    Spacer()
                    if let latest = series.latest {
                        Text(MetricSeries.display(latest.text, unit: series.unit)).font(.caption.monospacedDigit())
                        Text(latest.date).font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Text("no data").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !anyValues {
                Text("No values in this range.").font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 60)
            } else {
                ChannelChart(
                    points: plotPoints, gaps: gapMarks, dates: dates, labelledDates: labelledDates,
                    plotsHours: plotsHours)
                .frame(height: 150)
            }
            ForEach(channels, id: \.provider) { series in
                HStack(spacing: 10) {
                    Text(ProviderLabel.display(series.provider)).font(.caption2.bold()).foregroundStyle(color(for: series.provider))
                    Text("\(series.daysWithValues) with values").font(.caption2).foregroundStyle(.secondary)
                    if series.daysWithoutRecords > 0 {
                        Text("\(series.daysWithoutRecords) without records").font(.caption2).foregroundStyle(.secondary)
                    }
                    if series.daysUnusable > 0 {
                        Text("\(series.daysUnusable) without a reliable value").font(.caption2).foregroundStyle(.orange)
                    }
                    Spacer()
                    Text(series.statistic.replacingOccurrences(of: "_", with: " ")).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }

    private func color(for provider: String) -> Color {
        switch provider {
        case "healthkit": return .accentColor
        case "google_health": return .green
        default: return .purple
        }
    }
}

struct MetricFailureCard: View {
    let metric: Metric
    let message: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(metric.rawValue.replacingOccurrences(of: "_", with: " ").capitalized).font(.subheadline.bold())
            Label(message, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.red)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }
}

/// The tool returned no provider at all: no records of this metric in the range.
struct MetricEmptyCard: View {
    let metric: Metric
    let range: HomeModel.Range

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(metric.rawValue.replacingOccurrences(of: "_", with: " ").capitalized).font(.subheadline.bold())
                Spacer()
                Text("no data").font(.subheadline).foregroundStyle(.secondary)
            }
            Text("No records in the past \(range.rawValue) days.").font(.caption).foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }
}

struct ChartPoint: Identifiable {
    let id: String
    let date: String
    let y: Double
    let run: String
    let color: Color
}

struct ChartGap: Identifiable {
    let id: String
    let date: String
    let color: Color
}

/// The chart itself, fed with pre-computed typed arrays so SwiftUI's type checker stays fast.
struct ChannelChart: View {
    let points: [ChartPoint]
    let gaps: [ChartGap]
    let dates: [String]
    let labelledDates: [String]
    let plotsHours: Bool

    var body: some View {
        Chart {
            ForEach(points) { point in
                LineMark(x: .value("Day", point.date), y: .value("Value", point.y), series: .value("Run", point.run))
                    .foregroundStyle(point.color)
            }
            ForEach(points) { point in
                PointMark(x: .value("Day", point.date), y: .value("Value", point.y))
                    .foregroundStyle(point.color)
            }
            ForEach(gaps) { gap in
                RuleMark(x: .value("Day", gap.date))
                    .foregroundStyle(gap.color)
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
            }
        }
        .chartLegend(.hidden)
        .chartXScale(domain: dates)
        .chartYScale(domain: .automatic(includesZero: false))
        .chartXAxis {
            AxisMarks(values: labelledDates) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let day = value.as(String.self) { Text(String(day.suffix(5))).font(.caption2) }
                }
            }
        }
        .chartYAxis {
            AxisMarks(position: .trailing) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let number = value.as(Double.self) { Text(axisLabel(number)).font(.caption2) }
                }
            }
        }
    }

    private func axisLabel(_ number: Double) -> String {
        plotsHours ? String(format: "%.1f h", number) : number.formatted(.number.precision(.fractionLength(0...1)))
    }
}
