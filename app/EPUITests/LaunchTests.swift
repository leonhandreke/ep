import XCTest

/// The app launches and shows its main window. The harness (stage 2) builds on this.
final class LaunchTests: XCTestCase {
    @MainActor
    func testLaunchShowsMainWindow() throws {
        let app = XCUIApplication()
        // A port nothing listens on: the test needs no backend and touches no settings.
        app.launchEnvironment["EP_SERVER_URL"] = "http://127.0.0.1:9"
        app.launchArguments += ["-ApplePersistenceIgnoreState", "YES"]
        app.launch()

        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["toolbar.section"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["connection.banner"].waitForExistence(timeout: 10))
    }
}
