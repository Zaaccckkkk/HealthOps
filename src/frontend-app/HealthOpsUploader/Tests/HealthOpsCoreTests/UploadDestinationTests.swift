import Foundation
import XCTest
@testable import HealthOpsCore

final class UploadDestinationTests: XCTestCase {
    let cloud = URL(string: "https://personal.example.test")!

    func testPhysicalPhoneFailsClosedForUnknownOrSyntheticEnvironment() {
        for mode: String? in [nil, "development", "synthetic", "future-mode"] {
            XCTAssertNotNil(UploadDestination.rejection(server: cloud, mode: mode, simulator: false))
        }
        XCTAssertNil(UploadDestination.rejection(server: cloud, mode: "personal", simulator: false))
        XCTAssertNotNil(UploadDestination.rejection(server: URL(string: "http://personal.example.test")!,
                                                    mode: "personal", simulator: false))
    }

    func testSimulatorCannotUploadToPersonalEnvironment() {
        XCTAssertNotNil(UploadDestination.rejection(server: cloud, mode: "personal", simulator: true))
        XCTAssertNil(UploadDestination.rejection(server: cloud, mode: "synthetic", simulator: true))
        XCTAssertEqual(UploadDestination.dataKind(simulator: false), "personal")
        XCTAssertEqual(UploadDestination.dataKind(simulator: true), "synthetic")
    }

    func testUnsafeURLAndMissingServerDeclarationAreRejected() throws {
        let user = try JSONDecoder().decode(UserInfo.self, from: Data("{\"id\":\"a\",\"display_name\":\"A\"}".utf8))
        XCTAssertNil(user.dataMode)
        XCTAssertNotNil(UploadDestination.rejection(server: cloud, mode: user.dataMode, simulator: false))
        for text in ["https://user:password@personal.example.test", "https://personal.example.test?token=x"] {
            XCTAssertNotNil(UploadDestination.rejection(server: URL(string: text)!, mode: "personal", simulator: false))
        }
    }
}
