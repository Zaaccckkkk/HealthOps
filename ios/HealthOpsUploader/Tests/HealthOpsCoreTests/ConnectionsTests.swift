import Foundation
import XCTest
@testable import HealthOpsCore

final class ConnectionsTests: XCTestCase {
    func testStatusDecodesWithReportAndTolerantMissingFields() throws {
        let json = """
        {"configured": true, "connected": true, "connected_at": "2026-10-03T05:00:00Z", "scopes": ["a"],
         "linked_account": true, "last_session_outcome": "connected",
         "last_sync_report": {"window_start": "2026-09-20", "window_end": "2026-10-03", "timezone": "America/New_York",
           "types": [{"data_type": "steps", "fetched": 14, "inserted": 14, "duplicates": 0, "conflicts": [], "unmapped": 0, "error": null},
                     {"data_type": "sleep", "fetched": 0, "inserted": 0, "duplicates": 0, "conflicts": ["sleep:x"], "unmapped": 0, "error": "google 503: down"}]}}
        """
        let status = try JSONDecoder().decode(ConnectionStatus.self, from: Data(json.utf8))
        XCTAssertTrue(status.connected)
        XCTAssertEqual(status.lastSyncReport?.inserted, 14)
        XCTAssertEqual(status.lastSyncReport?.conflicts, 1)
        XCTAssertEqual(status.lastSyncReport?.failedTypes, ["sleep"])
        let minimal = try JSONDecoder().decode(ConnectionStatus.self, from: Data("{\"configured\": false, \"connected\": false}".utf8))
        XCTAssertFalse(minimal.connected)
    }

    func testProviderLabelsAndOutcomeTexts() {
        XCTAssertEqual(ProviderLabel.display("healthkit"), "Apple Health")
        XCTAssertEqual(ProviderLabel.display("google_health"), "Google Health")
        XCTAssertEqual(ProviderLabel.display(nil), "All channels")
        XCTAssertTrue(ConnectionOutcome.describe("account_not_linked").contains("not linked"))
        XCTAssertTrue(ConnectionOutcome.describe("invalid_state").contains("expired"))
    }
}
