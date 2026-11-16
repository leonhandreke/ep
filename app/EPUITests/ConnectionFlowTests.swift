import XCTest

/// Changing the server address in Settings changes the connection, in Settings and in
/// the main window.
final class ConnectionFlowTests: EPUITestCase {
    func testChangingServerAddressChangesConnection() async throws {
        let api = try Harness.requireAPI()
        // The saved address (uitest sets it to the private backend), so Settings can edit it.
        let app = try launch(server: .saved)
        let main = app.window(containing: "artists.column")
        // Connected: the artists column lists the seeded artists.
        waitFor(main.row("artists", id: Seed.bjork), timeout: 15)

        app.typeKey(",", modifierFlags: .command)
        let settings = app.window(containing: "settings.serverURL")
        let address = waitFor(settings.element("settings.serverURL"))
        let status = settings.element("settings.connectionStatus")
        waitForText("Connected", in: status)

        // The server's own counts, as Settings shows them.
        let server = try await Harness.get("server") as? [String: Any]
        let artists = (server?["counts"] as? [String: Any])?["artists"] as? Int
        XCTAssertNotNil(artists, "GET /server has no counts.artists")
        waitForText("\(artists ?? -1) artists", in: settings.element("settings.serverInfo"))

        // An address where nothing listens: not reachable, and the main window says so.
        address.replaceText(with: "127.0.0.1:9")
        waitForText("Not reachable", in: status)
        waitFor(main.element("connection.banner"))
        // What was loaded stays visible while unreachable.
        XCTAssertTrue(main.row("artists", id: Seed.bjork).exists)

        // Back to the private backend.
        address.replaceText(with: api.absoluteString)
        waitForText("Connected", in: status)
        waitForAbsence(main.element("connection.banner"))
    }
}
