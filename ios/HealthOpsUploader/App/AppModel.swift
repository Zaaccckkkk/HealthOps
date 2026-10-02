import Foundation
import HealthKit
import HealthOpsCore
import SwiftUI

/// Application state. Identity comes from the server (`/v1/me`); consent and sync state are
/// scoped to server + user, so changing either never reuses the previous scope.
@MainActor
final class AppModel: ObservableObject {
    @Published var serverURLText: String {
        didSet { serverTextChanged() }
    }
    @Published var tokenDraft = ""
    @Published private(set) var hasToken: Bool
    /// Verified identity bound to the exact server URL and credential version that produced it.
    @Published private(set) var identity = IdentitySession()
    @Published private(set) var consent: ConsentRecord?
    @Published private(set) var healthKitRequested = false
    @Published private(set) var states: [SyncedType: SyncState] = [:]
    @Published private(set) var syncing = false
    @Published private(set) var messages: [String] = []
    @Published var selectedMetrics: Set<Metric> = Set(SyncedType.allCases.map(\.metric))
    @Published var historyStart: Date = Calendar.current.date(byAdding: .day, value: -30, to: Date())!

    let healthStore = HKHealthStore()
    private let defaults = UserDefaults.standard
    private let syncStore: FileSyncStore
    private let consentStore: FileConsentStore
    private var syncTask: Task<Void, Never>?

    static let pageSize = 200

    /// Everything a sync run needs, captured once when it starts. Later changes to the token,
    /// server, user or consent cancel the run instead of leaking into it.
    struct SyncContext {
        let serverURL: URL
        let user: UserInfo
        let token: String
        let consent: ConsentRecord
    }

    init() {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("HealthOpsUploader", isDirectory: true)
        syncStore = try! FileSyncStore(directory: support.appendingPathComponent("sync"))
        consentStore = try! FileConsentStore(file: support.appendingPathComponent("consent.json"))
        serverURLText = defaults.string(forKey: "serverURL") ?? "http://127.0.0.1:8000"
        hasToken = KeychainTokenStore.load() != nil
        consent = try? consentStore.load()
        identity.serverChanged(to: serverURL)
    }

    /// The user verified against the current server and credential, or nil.
    var user: UserInfo? { identity.current?.user }

    private func serverTextChanged() {
        if identity.serverChanged(to: serverURL) {
            cancelSync(reason: "server changed")
            note("Server changed; verify identity again before granting consent or syncing.")
            reloadStates()
        }
    }

    var serverURL: URL? {
        guard let url = URL(string: serverURLText.trimmingCharacters(in: .whitespaces)),
              let scheme = url.scheme, ["http", "https"].contains(scheme), url.host != nil
        else { return nil }
        return url
    }

    var consentCoversCurrentScope: Bool {
        guard let consent, let user, let serverURL else { return false }
        return consent.covers(serverURL: serverURL, userID: user.id)
    }

    var healthDataAvailable: Bool { HKHealthStore.isHealthDataAvailable() }

    private func note(_ text: String) {
        messages.insert("\(Self.clock.string(from: Date()))  \(text)", at: 0)
        if messages.count > 60 { messages.removeLast() }
    }

    private static let clock: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter
    }()

    // MARK: Server and identity

    func saveToken() {
        cancelSync(reason: "credentials changed")
        KeychainTokenStore.save(tokenDraft.trimmingCharacters(in: .whitespacesAndNewlines))
        tokenDraft = ""
        hasToken = KeychainTokenStore.load() != nil
        // Any in-flight /v1/me response for the previous token is now stale and will be ignored.
        identity.credentialsChanged()
        reloadStates()
        note(hasToken ? "Token stored in the Keychain. Verify identity again." : "Token cleared.")
    }

    /// Stops a running sync before its next device read or request. A request already in
    /// flight cannot be recalled; its page stays pending and is handled by the next consent.
    func cancelSync(reason: String) {
        guard let task = syncTask else { return }
        task.cancel()
        syncTask = nil
        note("Sync cancelled (\(reason)). A request already in flight cannot be recalled.")
    }

    func connect() async {
        guard let serverURL else {
            note("Server URL must be http(s) with a host.")
            return
        }
        guard let token = KeychainTokenStore.load() else {
            note("Store a development token first.")
            return
        }
        defaults.set(serverURLText, forKey: "serverURL")
        guard let attempt = identity.beginVerification() else { return }
        let previous = user
        do {
            let me = try await APIClient(baseURL: serverURL, token: token).me()
            // Accept the response only if the server and credential are still the ones used.
            guard identity.complete(attempt, user: me) else {
                note("Ignored a stale identity response: server or token changed meanwhile.")
                return
            }
            if let previous, previous.id != me.id {
                cancelSync(reason: "identity changed")
                note("Different user than before; previous consent and queues are not reused.")
            }
            note("Connected as \(me.displayName) (user \(me.id.prefix(8))…) on \(serverURL.host ?? "").")
            reloadStates()
        } catch {
            if identity.beginVerification() == attempt {
                note("Connection failed: \(SyncEngine.describe(error))")
            }
        }
    }

    // MARK: HealthKit authorization (reading on the phone; not upload consent)

    func requestHealthKitAuthorization() async {
        guard healthDataAvailable else {
            note("HealthKit is not available on this device.")
            return
        }
        var share: Set<HKSampleType> = []
        #if DEBUG && targetEnvironment(simulator)
        share = SyntheticSeeder.writeTypes()
        #endif
        do {
            try await healthStore.requestAuthorization(
                toShare: share, read: Set(SyncedType.allCases.map(\.sampleType)))
            healthKitRequested = true
            note("HealthKit authorization sheet completed. HealthKit does not reveal denied read types.")
        } catch {
            note("HealthKit authorization failed: \(error.localizedDescription)")
        }
    }

    // MARK: Upload consent (sending data off the phone)

    func grantConsent() {
        // Consent requires an identity verified against the current server and credential.
        guard let verified = identity.current, let serverURL, verified.serverURL == serverURL else {
            note("Verify identity for this server and token before granting consent.")
            return
        }
        let user = verified.user
        cancelSync(reason: "consent changed")
        // Midnight of the chosen day in the device zone, so the bound is a whole local day.
        let start = Calendar.current.startOfDay(for: historyStart)
        let record = ConsentRecord(
            serverURL: serverURL, userID: user.id, enabledMetrics: selectedMetrics,
            historyStart: start, grantedAt: Date())
        do {
            try consentStore.save(record)
            consent = record
            // Pages read under an earlier consent with a different history range are never
            // replayed automatically.
            let discarded = try SyncMaintenance.discardPendingOutsideConsent(
                store: syncStore, serverURL: serverURL, userID: user.id, historyStart: start)
            note("Upload consent recorded for this server and user.")
            if discarded > 0 {
                note("Discarded \(discarded) pending page(s) read under a previous history range; they were never uploaded.")
            }
            reloadStates()
        } catch {
            note("Could not save consent: \(error.localizedDescription)")
        }
    }

    func revokeConsent() {
        cancelSync(reason: "consent revoked")
        try? consentStore.save(nil)
        consent = nil
        note("Consent revoked. No further requests are sent; records already on the server are not deleted.")
    }

    // MARK: Sync

    func scope(for type: SyncedType) -> SyncScope? {
        guard let verified = identity.current, let serverURL, verified.serverURL == serverURL,
              let consent, consent.covers(serverURL: serverURL, userID: verified.user.id) else { return nil }
        let user = verified.user
        return SyncScope(
            serverURL: serverURL, userID: user.id, typeIdentifier: type.identifier,
            historyStart: consent.historyStart)
    }

    private func scope(for type: SyncedType, in context: SyncContext) -> SyncScope {
        SyncScope(
            serverURL: context.serverURL, userID: context.user.id, typeIdentifier: type.identifier,
            historyStart: context.consent.historyStart)
    }

    func reloadStates() {
        var loaded: [SyncedType: SyncState] = [:]
        for type in SyncedType.allCases {
            if let scope = scope(for: type) { loaded[type] = try? syncStore.load(scope) }
        }
        states = loaded
    }

    func sync() {
        guard syncTask == nil else {
            note("A sync is already running.")
            return
        }
        guard let verified = identity.current, let serverURL, verified.serverURL == serverURL,
              let consent, consentCoversCurrentScope, let token = KeychainTokenStore.load() else {
            note("Verify identity and grant consent for this server and token before syncing.")
            return
        }
        let context = SyncContext(serverURL: serverURL, user: verified.user, token: token, consent: consent)
        syncing = true
        syncTask = Task { [weak self] in
            await self?.run(context)
            await MainActor.run {
                self?.syncing = false
                self?.syncTask = nil
            }
        }
    }

    private func run(_ context: SyncContext) async {
        let client = APIClient(baseURL: context.serverURL, token: context.token)
        var inserted = 0, duplicates = 0, uploads = 0, unmappable = 0
        for type in SyncedType.allCases where context.consent.enabledMetrics.contains(type.metric) {
            if Task.isCancelled { return }
            let scope = scope(for: type, in: context)
            let engine = SyncEngine(
                scope: scope,
                source: HealthKitSampleSource(
                    store: healthStore, type: type, notBefore: context.consent.historyStart),
                uploader: client, store: syncStore, enabledMetrics: context.consent.enabledMetrics,
                pageSize: Self.pageSize)
            let report = await engine.sync()
            inserted += report.inserted
            duplicates += report.duplicates
            uploads += report.uploadsSent
            unmappable += report.mappingFailures
            if report.cancelled { return }
            if let error = report.error {
                note("\(type.metric.rawValue): \(error)\(report.pendingRetained ? " Pending page kept for replay." : "")")
                reloadStates()
                return
            }
        }
        reloadStates()
        var summary = uploads == 0
            ? "No new samples; nothing was sent."
            : "Sync complete: \(uploads) request(s), inserted \(inserted), duplicates \(duplicates)."
        if unmappable > 0 {
            summary += " \(unmappable) sample(s) could not be mapped and were recorded, not uploaded."
        }
        note(summary)
    }

    #if DEBUG
    /// Debug only: forget committed anchors so every sample is read again. The server must
    /// answer with duplicates only; this demonstrates replay safety end to end.
    func resetAnchorsForReplayDemo() {
        for type in SyncedType.allCases {
            guard let scope = scope(for: type), var state = try? syncStore.load(scope) else { continue }
            state.committedAnchor = nil
            state.pending = nil
            try? syncStore.save(state, for: scope)
        }
        reloadStates()
        note("Anchors reset (debug). The next sync re-reads everything; expect duplicates only.")
    }
    #endif

    #if DEBUG && targetEnvironment(simulator)
    func seedSyntheticSamples() async {
        do {
            let count = try await SyntheticSeeder.seed(into: healthStore, around: Date())
            note("Wrote \(count) synthetic samples into the Simulator's HealthKit store.")
        } catch {
            note("Seeding failed: \(error.localizedDescription)")
        }
    }
    #endif
}
