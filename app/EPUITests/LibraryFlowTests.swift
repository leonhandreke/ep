import XCTest

/// Browsing the Library: filter the artists, choose an artist and an album, see its
/// tracks, and go back.
final class LibraryFlowTests: EPUITestCase {
    func testFilterSelectAndGoBack() throws {
        let app = try launchConnected()
        let bjork = app.row("artists", id: Seed.bjork)

        // The filter asks the server: "bjo" keeps Björk and drops Fennesz.
        let filter = app.element("artists.filter")
        filter.click()
        filter.typeText("bjo")
        waitForAbsence(app.row("artists", id: Seed.fennesz))
        waitFor(bjork).click()

        waitForText("Björk", in: app.element("releaseGroups.header"))
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()

        waitForText("Homogenic", in: app.element("tracks.header"))
        waitFor(app.row("tracks", id: Seed.jogaTrack))
        XCTAssertTrue(app.element("toolbar.back").isEnabled)

        // Back: the artist without an album, so no tracks.
        app.typeKey("[", modifierFlags: .command)
        waitForAbsence(app.row("tracks", id: Seed.jogaTrack))
        waitForText("Björk", in: app.element("releaseGroups.header"))

        // Forward: the album again.
        app.element("toolbar.forward").click()
        waitFor(app.row("tracks", id: Seed.jogaTrack))
    }

    /// Type-to-select in the artists list, and ← / → between the three columns.
    func testKeyboardNavigation() throws {
        let app = try launchConnected()

        // Click a row to focus the list, then type: "fen" selects Fennesz.
        app.row("artists", id: Seed.bjork).click()
        app.typeText("fen")
        waitForText("Fennesz", in: app.element("releaseGroups.header"))

        // →: the first album is selected, its tracks show; →: the first track is selected.
        app.typeKey(.rightArrow, modifierFlags: [])
        waitFor(app.element("tracks.list"))
        app.typeKey(.rightArrow, modifierFlags: [])
        waitFor(app.element("detail.track.title"))

        // ← twice: back at the artists, which stay selected; ↓ moves to the next artist.
        app.typeKey(.leftArrow, modifierFlags: [])
        app.typeKey(.leftArrow, modifierFlags: [])
        app.typeKey(.downArrow, modifierFlags: [])
        waitForText("Mùm", in: app.element("detail.artist.title"))
    }
}
