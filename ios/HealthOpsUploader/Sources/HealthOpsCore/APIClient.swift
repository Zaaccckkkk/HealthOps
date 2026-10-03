import Foundation

public struct UserInfo: Codable, Equatable, Sendable {
    public var id: String
    public var displayName: String
    enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
    }
}

/// Bearer-token client for the local development API. The token is supplied by the caller
/// (Keychain in the app) and never logged.
public final class APIClient: Uploader, @unchecked Sendable {
    public let baseURL: URL
    private let token: String
    private let session: URLSession

    public init(baseURL: URL, token: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.token = token
        self.session = session
    }

    public func me() async throws -> UserInfo {
        let (data, status) = try await send("GET", path: "/v1/me", body: nil)
        guard status == 200 else { throw Self.error(for: status, data: data) }
        return try ContractEncoding.decoder().decode(UserInfo.self, from: data)
    }

    public func upload(body: Data) async throws -> BatchResult {
        let (data, status) = try await send("POST", path: "/v1/observations/batch", body: body)
        guard status == 200 else { throw Self.error(for: status, data: data) }
        return try ContractEncoding.decoder().decode(BatchResult.self, from: data)
    }

    /// Single-turn question. The server deadline is 120 s, so the client waits a little longer
    /// and reports anything else as a failure category the screen can show.
    public func ask(_ ask: AskRequest) async throws -> AskResponse {
        let body = try ContractEncoding.encoder().encode(ask)
        let data: Data
        let status: Int
        do {
            (data, status) = try await send("POST", path: "/v1/assistant/ask", body: body, timeout: 130)
        } catch is CancellationError {
            throw ServiceFailure.cancelled
        } catch let UploadError.network(detail) {
            throw ServiceFailure.unavailable(detail)
        }
        switch status {
        case 200:
            do { return try JSONDecoder().decode(AskResponse.self, from: data) }
            catch { throw ServiceFailure.decoding(String(describing: error).prefix(200).description) }
        case 401: throw ServiceFailure.unauthorized
        case 422:
            if case let .invalid(locations) = Self.error(for: status, data: data) {
                throw ServiceFailure.invalidRequest(locations)
            }
            throw ServiceFailure.invalidRequest("422")
        case 429: throw ServiceFailure.rateLimited
        case 502, 503, 504: throw ServiceFailure.unavailable("HTTP \(status)")
        default: throw ServiceFailure.server(status)
        }
    }

    /// Deterministic per-day values straight from the tool route; no model is involved.
    public func dailySummary(
        metric: Metric, startDate: String, endDate: String, timezone: String, provider: String?
    ) async throws -> DailySummaryResponse {
        var query = [
            URLQueryItem(name: "metric", value: metric.rawValue),
            URLQueryItem(name: "timezone", value: timezone),
            URLQueryItem(name: "start_date", value: startDate),
            URLQueryItem(name: "end_date", value: endDate),
        ]
        if let provider { query.append(URLQueryItem(name: "provider", value: provider)) }
        let data: Data
        let status: Int
        do {
            (data, status) = try await send("GET", path: "/v1/tools/daily-summary", body: nil, query: query)
        } catch is CancellationError {
            throw ServiceFailure.cancelled
        } catch let UploadError.network(detail) {
            throw ServiceFailure.unavailable(detail)
        }
        switch status {
        case 200:
            do { return try JSONDecoder().decode(DailySummaryResponse.self, from: data) }
            catch { throw ServiceFailure.decoding(String(describing: error).prefix(200).description) }
        case 401: throw ServiceFailure.unauthorized
        case 422: throw ServiceFailure.invalidRequest("422")
        case 429: throw ServiceFailure.rateLimited
        case 502, 503, 504: throw ServiceFailure.unavailable("HTTP \(status)")
        default: throw ServiceFailure.server(status)
        }
    }

    // MARK: Google Health connection (server-side connector)

    public func googleHealthSession(timezone: String) async throws -> ConnectionSessionResponse {
        try await connection("POST", path: "/v1/connections/google-health/session", json: ["timezone": timezone])
    }

    public func googleHealthStatus() async throws -> ConnectionStatus {
        try await connection("GET", path: "/v1/connections/google-health", json: nil)
    }

    public func googleHealthSync(timezone: String, days: Int?) async throws -> ConnectorSyncReport {
        var body: [String: Any] = ["timezone": timezone]
        if let days { body["days"] = days }
        return try await connection("POST", path: "/v1/connections/google-health/sync", json: body, timeout: 180)
    }

    public func googleHealthDisconnect() async throws -> DisconnectResult {
        try await connection("DELETE", path: "/v1/connections/google-health", json: nil)
    }

    public func googleHealthDeleteData() async throws -> DeleteDataResult {
        try await connection("DELETE", path: "/v1/connections/google-health/data", json: nil)
    }

    private func connection<T: Decodable>(
        _ method: String, path: String, json: [String: Any]?, timeout: TimeInterval = 30
    ) async throws -> T {
        let body = try json.map { try JSONSerialization.data(withJSONObject: $0) }
        let data: Data
        let status: Int
        do {
            (data, status) = try await send(method, path: path, body: body, timeout: timeout)
        } catch is CancellationError {
            throw ServiceFailure.cancelled
        } catch let UploadError.network(detail) {
            throw ServiceFailure.unavailable(detail)
        }
        switch status {
        case 200:
            do { return try JSONDecoder().decode(T.self, from: data) }
            catch { throw ServiceFailure.decoding(String(describing: error).prefix(200).description) }
        case 401: throw ServiceFailure.unauthorized
        case 404: throw ServiceFailure.invalidRequest("not connected")
        case 409: throw ServiceFailure.invalidRequest("reconnect required")
        case 422: throw ServiceFailure.invalidRequest("422")
        case 429: throw ServiceFailure.rateLimited
        case 502, 503, 504: throw ServiceFailure.unavailable("HTTP \(status)")
        default: throw ServiceFailure.server(status)
        }
    }

    private func send(
        _ method: String, path: String, body: Data?, timeout: TimeInterval = 20, query: [URLQueryItem] = []
    ) async throws -> (Data, Int) {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = timeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            return (data, status)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw UploadError.network((error as NSError).localizedDescription)
        }
    }

    static func error(for status: Int, data: Data) -> UploadError {
        switch status {
        case 401: return .unauthorized
        case 409: return .conflict
        case 422:
            // The server already omits input values; keep only field locations here.
            struct Detail: Decodable { var detail: [Item]? ; struct Item: Decodable { var loc: [Loc]? }
                enum Loc: Decodable { case s(String), i(Int)
                    init(from decoder: Decoder) throws {
                        let c = try decoder.singleValueContainer()
                        if let i = try? c.decode(Int.self) { self = .i(i) } else { self = .s(try c.decode(String.self)) }
                    }
                    var text: String { switch self { case let .s(s): return s; case let .i(i): return String(i) } }
                } }
            let items = (try? JSONDecoder().decode(Detail.self, from: data))?.detail ?? []
            let locations = items.compactMap { $0.loc?.map(\.text).joined(separator: ".") }
            return .invalid(locations.isEmpty ? "422" : locations.joined(separator: ", "))
        default: return .server(status)
        }
    }
}
