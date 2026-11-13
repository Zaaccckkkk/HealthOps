import Foundation
import XCTest
@testable import HealthOpsCore

private final class UploadProtocol: URLProtocol, @unchecked Sendable {
    static var observed: URLRequest?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.observed = request
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data("{\"received\":1,\"inserted\":1,\"duplicates\":0}".utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

final class APIClientUploadTests: XCTestCase {
    func testUploadCarriesAccountTokenAndDeclaredDataKind() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [UploadProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        for kind in ["personal", "synthetic"] {
            let client = APIClient(baseURL: URL(string: "https://fixture.example.test")!,
                                   token: "fixture-not-a-real-token", session: session, uploadDataKind: kind)
            _ = try await client.upload(body: Data("{\"observations\":[]}".utf8))
            XCTAssertEqual(UploadProtocol.observed?.value(forHTTPHeaderField: "X-HealthOps-Data-Kind"), kind)
            XCTAssertEqual(UploadProtocol.observed?.value(forHTTPHeaderField: "Authorization"), "Bearer fixture-not-a-real-token")
            XCTAssertEqual(UploadProtocol.observed?.url?.path, "/v1/observations/batch")
        }
    }
}
