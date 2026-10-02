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

    private func send(_ method: String, path: String, body: Data?) async throws -> (Data, Int) {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = 20
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            return (data, status)
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
