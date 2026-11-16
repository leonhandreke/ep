import XCTest

/// Playing from the Library: a track and its album's rest in the queue, Space, Show
/// playing (⌘L); a radio excerpt on its episode's scrubber; a link-only track.
final class PlayerFlowTests: EPUITestCase {
    func testPlayQueueSpaceAndShowPlaying() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")
        waitFor(player.element("player.nothingPlaying"))
        XCTAssertFalse(player.element("player.showPlaying").isEnabled)

        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.hunterTrack)).doubleClick()

        // Hunter plays (the seeded files are 4 s long: pause it at once). Space pauses:
        // focus is in the tracks list, not a text field.
        waitForText("Hunter", in: player.element("player.title"))
        app.typeKey(" ", modifierFlags: [])
        waitForText("Play (Space)", in: player.element("player.playPause"))

        // Jóga, the album's other track that plays, is up next.
        waitForText("Björk — Homogenic", in: player.element("player.subtitle"))
        waitForText("1 up next", in: player.element("player.queue"))
        waitFor(app.element("tracks.nowPlaying"))

        // Elsewhere in the Library, ⌘L comes back to what plays.
        app.typeKey("[", modifierFlags: .command)
        waitForAbsence(app.row("tracks", id: Seed.hunterTrack))
        app.typeKey("l", modifierFlags: .command)
        waitFor(app.row("tracks", id: Seed.hunterTrack))
        waitForText("Hunter", in: app.element("detail.track.title"))
    }

    func testExcerptOnTheEpisodesScrubberAndLinkOnly() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.vespertine)).click()
        waitFor(app.row("tracks", id: Seed.cocoonTrack)).doubleClick()

        // Cocoon plays inside "Strings only": the episode's context and its marks, both
        // at estimated times.
        waitForText("Late Night Tapes", in: player.element("player.context"))
        waitForText("1 of 2", in: player.element("player.context"))
        waitForText("Cocoon", in: player.element("player.title"))
        waitForText("2 tracklist marks, 2 estimated", in: player.element("player.scrubber"))

        // Bachelorette is only a Bandcamp link: nothing plays, the bar says why.
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.bacheloretteTrack)).doubleClick()
        waitForText("only a Bandcamp link", in: player.element("player.notice.text"))
        waitFor(player.element("player.notice.open"))
        waitForAbsence(player.element("player.scrubber"))
    }
}
