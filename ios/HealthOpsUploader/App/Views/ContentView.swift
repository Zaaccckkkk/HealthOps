import HealthOpsCore
import SwiftUI

struct ContentView: View {
    enum Tab: Hashable { case home, ask, setup }

    @EnvironmentObject private var model: AppModel
    @State private var selectedTab: Tab = .home

    var body: some View {
        TabView(selection: $selectedTab) {
            HomeView(home: model.home, selectedTab: $selectedTab)
                .tabItem { Label("Overview", systemImage: "chart.xyaxis.line") }
                .tag(Tab.home)
            ChatView(assistant: model.assistant)
                .tabItem { Label("Ask", systemImage: "text.bubble") }
                .tag(Tab.ask)
            SetupView()
                .tabItem { Label("Setup", systemImage: "gearshape") }
                .tag(Tab.setup)
        }
    }
}

/// Server, account, Health access, upload consent and sync. Numbered as the onboarding order.
struct SetupView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                serverSection
                healthKitSection
                consentSection
                syncSection
                #if DEBUG
                debugSection
                #endif
                logSection
            }
            .navigationTitle("Setup")
        }
    }

    private var serverSection: some View {
        Section("1. Server and account") {
            TextField("Server URL", text: $model.serverURLText)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
            SecureField("Development token (stored in Keychain)", text: $model.tokenDraft)
            HStack {
                Button("Save token") { model.saveToken() }
                    .disabled(model.tokenDraft.isEmpty)
                Spacer()
                Text(model.hasToken ? "Token present" : "No token").foregroundStyle(.secondary)
            }
            Button("Connect and verify identity") { Task { await model.connect() } }
                .disabled(!model.hasToken || model.serverURL == nil)
            if let user = model.user {
                LabeledContent("Server user", value: "\(user.displayName) · \(user.id.prefix(8))…")
            }
        }
    }

    private var healthKitSection: some View {
        Section("2. HealthKit read access (on this phone)") {
            Button("Request HealthKit authorization") {
                Task { await model.requestHealthKitAuthorization() }
            }
            .disabled(!model.healthDataAvailable)
            Text("Reading on the phone is separate from uploading. Nothing leaves the phone until step 3.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    private var consentSection: some View {
        Section("3. Upload consent (sending data to the server)") {
            if model.consentCoversCurrentScope, let consent = model.consent {
                LabeledContent("Granted", value: consent.grantedAt.formatted(date: .abbreviated, time: .shortened))
                LabeledContent("History from", value: consent.historyStart.formatted(date: .abbreviated, time: .omitted))
                Text(consent.enabledMetrics.map(\.rawValue).sorted().joined(separator: ", "))
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Revoke consent", role: .destructive) { model.revokeConsent() }
            } else {
                ConsentForm()
            }
        }
    }

    private var syncSection: some View {
        Section("4. Sync") {
            Button(model.syncing ? "Syncing…" : "Sync now") { model.sync() }
                .disabled(model.syncing || !model.consentCoversCurrentScope)
            if model.syncing {
                Button("Cancel sync", role: .destructive) { model.cancelSync(reason: "requested") }
            }
            ForEach(SyncedType.allCases, id: \.self) { type in
                if let state = model.states[type] {
                    SyncRow(type: type, state: state)
                }
            }
            Text("Each page is stored before upload and its anchor commits only after the server confirms it. Deleted HealthKit samples are counted here but not removed from the server.")
                .font(.footnote).foregroundStyle(.secondary)
        }
    }

    #if DEBUG
    private var debugSection: some View {
        Section("Debug (not in release builds)") {
            #if targetEnvironment(simulator)
            Button("Write synthetic samples into the Simulator") {
                Task { await model.seedSyntheticSamples() }
            }
            #endif
            Button("Reset anchors to demonstrate replay") { model.resetAnchorsForReplayDemo() }
                .disabled(model.user == nil)
        }
    }
    #endif

    private var logSection: some View {
        Section("Activity") {
            ForEach(model.messages, id: \.self) { line in
                Text(line).font(.caption.monospaced())
            }
        }
    }
}

struct SyncRow: View {
    let type: SyncedType
    let state: SyncState

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(type.metric.rawValue).font(.subheadline.bold())
                Spacer()
                Text(state.pending == nil ? "committed" : "pending page kept")
                    .font(.caption)
                    .foregroundStyle(state.pending == nil ? Color.green : Color.orange)
            }
            Text("inserted \(state.totalInserted) · duplicates \(state.totalDuplicates) · deleted upstream, not synced: \(state.unsyncedDeletedCount)")
                .font(.caption).foregroundStyle(.secondary)
            if !state.unmappable.isEmpty {
                Text("not uploaded, could not be mapped: \(state.unmappable.count) (recorded)")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }
}

struct ConsentForm: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        if model.user == nil {
            Text("Connect first: consent is bound to the verified server user.")
                .font(.footnote).foregroundStyle(.secondary)
        } else {
            Text("Uploads raw HealthKit samples (value, unit, start/end time, source and device labels, sample UUID) to the server above, under the connected account. No display strings, no other Health data.")
                .font(.footnote)
            DatePicker("Upload history from", selection: $model.historyStart, in: ...Date(), displayedComponents: .date)
            ForEach(Metric.allCases.filter { metric in SyncedType.allCases.contains { $0.metric == metric } }, id: \.self) { metric in
                Toggle(metric.rawValue, isOn: Binding(
                    get: { model.selectedMetrics.contains(metric) },
                    set: { on in
                        if on { model.selectedMetrics.insert(metric) } else { model.selectedMetrics.remove(metric) }
                    }))
            }
            Button("I agree to upload the selected data") { model.grantConsent() }
                .disabled(model.selectedMetrics.isEmpty)
        }
    }
}
