import XCTest

/// Screenshot scenarios for `uitest shot <name>`: `testMain` is the scenario `main`.
/// Each brings the app into one state and captures it as `<name>` (in the appearance
/// `uitest` asks for; light unless `--dark`). They assert only what they need to reach
/// the state.
final class Scenarios: EPUITestCase {
    /// The main window connected to the seeded backend (Library).
    func testMain() throws {
        try launchConnected()
        screenshot("main")
    }

    /// The main window switched to Radio.
    func testRadio() throws {
        let app = try launchConnected()
        app.element("toolbar.section").radioButtons["Radio"].click()
        waitFor(app.element("shows.column"))
        screenshot("radio")
    }

    /// The server cannot be reached: banner with Retry and Settings….
    func testUnreachable() throws {
        let app = try launch(server: .unreachable)
        waitFor(app.element("connection.banner"))
        screenshot("unreachable")
    }

    /// The Settings window, connected (server version and counts).
    func testSettings() throws {
        let app = try launchConnected()
        app.typeKey(",", modifierFlags: .command)
        let settings = app.window(containing: "settings.serverURL")
        waitForText("Connected", in: settings.element("settings.connectionStatus"))
        waitFor(settings.element("settings.serverInfo"))
        screenshot("settings", of: settings)
    }
}
