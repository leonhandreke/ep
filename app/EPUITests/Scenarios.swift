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

    /// Library: Björk → Homogenic (every track state but slice and excerpt), Jóga selected.
    func testLibraryAlbum() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.jogaTrack)).click()
        waitForText("Jóga", in: app.element("detail.track.title"))
        screenshot("libraryAlbum")
    }

    /// Library: Björk's "Loose tracks" entry selected.
    func testLibraryLooseTracks() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: "looseTracks")).click()
        waitForText("Loose tracks", in: app.element("tracks.header"))
        waitFor(app.element("tracks.list"))
        screenshot("libraryLooseTracks")
    }

    /// Library: an artist with no releases (Mùm, credited on nothing).
    func testLibraryNoReleases() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.mum).click()
        waitFor(app.element("releaseGroups.empty"))
        screenshot("libraryNoReleases")
    }

    /// Library: Fennesz → Agora, an album with nothing that plays (banner).
    func testLibraryNoPlayable() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.fennesz).click()
        waitFor(app.row("releaseGroups", id: Seed.agora)).click()
        waitFor(app.element("tracks.banner"))
        screenshot("libraryNoPlayable")
    }

    /// Library: radio-only artists shown; Beverly Glenn-Copeland (known from radio only).
    func testLibraryRadioOnly() throws {
        let app = try launchConnected()
        app.element("artists.radioOnly").click()
        waitFor(app.row("artists", id: Seed.glennCopeland)).click()
        waitFor(app.element("releaseGroups.empty"))
        screenshot("libraryRadioOnly")
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
