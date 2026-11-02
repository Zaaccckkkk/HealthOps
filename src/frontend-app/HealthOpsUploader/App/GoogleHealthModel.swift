import AuthenticationServices
import Foundation
import HealthOpsCore
import SwiftUI

/// Google Health (Fitbit) connection state for the Setup tab. The browser session is opened
/// on a URL the server produced; the server stores the credential; the phone only reads the
/// status word from the callback URL and then asks the server for the connection state.
@MainActor
final class GoogleHealthModel: NSObject, ObservableObject, ASWebAuthenticationPresentationContextProviding {
    @Published private(set) var status: ConnectionStatus?
    @Published private(set) var busy = false
    @Published private(set) var message: String?
    @Published private(set) var lastReport: ConnectorSyncReport?

    private var authSession: ASWebAuthenticationSession?
    private var generation = 0

    func reset() {
        authSession?.cancel()
        authSession = nil
        generation += 1
        status = nil
        lastReport = nil
        message = nil
        busy = false
    }

    func refresh(client: APIClient) {
        let myGeneration = generation
        Task {
            do {
                let current = try await client.googleHealthStatus()
                guard myGeneration == self.generation else { return }
                self.status = current
                self.lastReport = current.lastSyncReport
            } catch let failure as ServiceFailure {
                guard myGeneration == self.generation else { return }
                self.message = failure.message
            } catch {
                guard myGeneration == self.generation else { return }
                self.message = (error as NSError).localizedDescription
            }
        }
    }

    func connect(client: APIClient) {
        guard !busy else { return }
        busy = true
        message = nil
        let myGeneration = generation
        Task {
            do {
                let session = try await client.googleHealthSession(timezone: TimeZone.current.identifier)
                guard myGeneration == self.generation else { return }
                self.openBrowser(session.authorizationURL, client: client, generation: myGeneration)
            } catch let failure as ServiceFailure {
                guard myGeneration == self.generation else { return }
                self.message = failure.message
                self.busy = false
            } catch {
                guard myGeneration == self.generation else { return }
                self.message = (error as NSError).localizedDescription
                self.busy = false
            }
        }
    }

    private func openBrowser(_ url: URL, client: APIClient, generation myGeneration: Int) {
        let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "healthops") { [weak self] callback, error in
            Task { @MainActor in
                guard let self, myGeneration == self.generation else { return }
                self.busy = false
                if let error {
                    self.message = (error as NSError).code == ASWebAuthenticationSessionError.canceledLogin.rawValue
                        ? "Authorization cancelled." : (error as NSError).localizedDescription
                    return
                }
                let status = callback.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }?
                    .queryItems?.first(where: { $0.name == "status" })?.value
                self.message = ConnectionOutcome.describe(status)
                self.refresh(client: client)
            }
        }
        session.presentationContextProvider = self
        session.prefersEphemeralWebBrowserSession = false
        authSession = session
        if !session.start() {
            busy = false
            message = "Could not open the browser for authorization."
        }
    }

    func sync(client: APIClient, days: Int) {
        guard !busy else { return }
        busy = true
        message = nil
        let myGeneration = generation
        Task {
            do {
                let report = try await client.googleHealthSync(timezone: TimeZone.current.identifier, days: days)
                guard myGeneration == self.generation else { return }
                self.lastReport = report
                var text = "Synced \(report.windowStart) to \(report.windowEnd): \(report.inserted) new record(s)."
                if report.conflicts > 0 { text += " \(report.conflicts) upstream revision(s) kept as conflicts." }
                if !report.failedTypes.isEmpty { text += " Failed: \(report.failedTypes.joined(separator: ", "))." }
                self.message = text
                self.refresh(client: client)
            } catch let failure as ServiceFailure {
                guard myGeneration == self.generation else { return }
                self.message = failure.message
            } catch {
                guard myGeneration == self.generation else { return }
                self.message = (error as NSError).localizedDescription
            }
            self.busy = false
        }
    }

    func disconnect(client: APIClient) {
        run(client: client) { try await client.googleHealthDisconnect() } describe: { result in
            result.revokedAtProvider
                ? "Disconnected; Google authorization revoked. Synced data stays until you delete it."
                : "Disconnected locally; Google did not confirm the revocation. Check your Google Account permissions."
        }
    }

    func deleteSyncedData(client: APIClient) {
        run(client: client) { try await client.googleHealthDeleteData() } describe: { result in
            "Deleted \(result.deleted) Google Health record(s) from the server."
        }
    }

    private func run<T>(client: APIClient, _ action: @escaping () async throws -> T, describe: @escaping (T) -> String) {
        guard !busy else { return }
        busy = true
        message = nil
        let myGeneration = generation
        Task {
            do {
                let result = try await action()
                guard myGeneration == self.generation else { return }
                self.message = describe(result)
                self.refresh(client: client)
            } catch let failure as ServiceFailure {
                guard myGeneration == self.generation else { return }
                self.message = failure.message
            } catch {
                guard myGeneration == self.generation else { return }
                self.message = (error as NSError).localizedDescription
            }
            self.busy = false
        }
    }

    nonisolated func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        ASPresentationAnchor()
    }
}
