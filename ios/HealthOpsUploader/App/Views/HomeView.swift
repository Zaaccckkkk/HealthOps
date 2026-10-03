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
                            ForEach(series, id: \.provider) { s in MetricChartCard(series: s) }
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

struct MetricChartCard: View {
    let series: MetricSeries

    private var title: String { series.metric.replacingOccurrences(of: "_", with: " ") }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(title.capitalized).font(.subheadline.bold())
                Spacer()
                if let latest = series.latest {
                    Text(MetricSeries.display(latest.text, unit: series.unit)).font(.subheadline.monospacedDigit())
                    Text(latest.date).font(.caption2).foregroundStyle(.secondary)
                } else {
                    Text("no data").font(.subheadline).foregroundStyle(.secondary)
                }
            }
            if series.daysWithValues == 0 {
                Text("No values in this range.").font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 60)
            } else {
                Chart {
                    ForEach(Array(series.segments.enumerated()), id: \.offset) { index, segment in
                        ForEach(segment) { point in
                            LineMark(x: .value("Day", point.date), y: .value(series.unit, point.value ?? 0))
                                .foregroundStyle(by: .value("Segment", "run \(index)"))
                            PointMark(x: .value("Day", point.date), y: .value(series.unit, point.value ?? 0))
                                .foregroundStyle(by: .value("Segment", "run \(index)"))
                        }
                    }
                    ForEach(series.points.filter { $0.quality != .value }) { point in
                        RuleMark(x: .value("Day", point.date))
                            .foregroundStyle(point.quality == .unusable ? Color.orange.opacity(0.5) : Color.secondary.opacity(0.25))
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    }
                }
                .chartLegend(.hidden)
                // Categorical axis in calendar order, gaps included; the value axis is not
                // forced through zero, so a 55-62 bpm series is readable.
                .chartXScale(domain: series.points.map(\.date))
                .chartYScale(domain: .automatic(includesZero: false))
                .chartForegroundStyleScale(range: [Color.accentColor, Color.accentColor, Color.accentColor, Color.accentColor, Color.accentColor, Color.accentColor])
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 4)) { value in
                        AxisValueLabel {
                            if let day = value.as(String.self) { Text(String(day.suffix(5))) }
                        }
                    }
                }
                .frame(height: 140)
            }
            HStack(spacing: 10) {
                Text("\(series.daysWithValues) day(s) with values").font(.caption2).foregroundStyle(.secondary)
                if series.daysWithoutRecords > 0 {
                    Text("\(series.daysWithoutRecords) without records").font(.caption2).foregroundStyle(.secondary)
                }
                if series.daysUnusable > 0 {
                    Text("\(series.daysUnusable) without a reliable value").font(.caption2).foregroundStyle(.orange)
                }
                Spacer()
                Text("\(series.provider) · \(series.statistic.replacingOccurrences(of: "_", with: " "))").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
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
