import XCTest

/// Playing from the Library: a track and its album's rest in the queue, Space, Show
/// playing (⌘L); a radio excerpt on its episode's scrubber; a link-only track.
final class PlayerFlowTests: EPUITestCase {
    func testPlayQueueSpaceAndShowPlaying() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")
        waitFor(player.element("player.nothingPlaying"))
        XCTAssertFalse(player.element("player.title").exists)

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
        waitForText("Now playing", in: app.row("tracks", id: Seed.hunterTrack))

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

    /// A space typed into the artist list belongs to type-to-select ("sigur r"), not to
    /// Space = play/pause.
    func testSpaceInTypeToSelectDoesNotTogglePlayback() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.hunterTrack)).doubleClick()
        waitForText("Pause (Space)", in: player.element("player.playPause"))
        app.typeKey(" ", modifierFlags: [])
        waitForText("Play (Space)", in: player.element("player.playPause"))

        app.row("artists", id: Seed.bjork).click()
        app.typeText("sigur r")
        waitFor(app.row("releaseGroups", id: Seed.agaetisByrjun))
        waitForText("Play (Space)", in: player.element("player.playPause"))
    }

    /// Every engine path really plays and seeks, judged by the bar's times (not by ear):
    /// FLAC/MP3 through AVPlayer, Ogg Vorbis and WebM/Opus through the decoding engine.
    func testEveryFormatPlaysAndSeeks() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")

        // Homogenic's Hunter: AVPlayer.
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        try assertPlaysAndSeeks(app, row: waitFor(app.row("tracks", id: Seed.hunterTrack)), title: "Hunter")

        // Svefn-g-englar: Ogg Vorbis.
        app.row("artists", id: Seed.sigurRos).click()
        waitFor(app.row("releaseGroups", id: Seed.agaetisByrjun)).click()
        try assertPlaysAndSeeks(
            app, row: waitFor(app.row("tracks", id: Seed.svefnTrack)), title: "Svefn-g-englar")

        // Loose Tone: WebM/Opus, a loose track of Björk's.
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.element("releaseGroups.row.looseTracks")).click()
        try assertPlaysAndSeeks(app, row: waitFor(app.row("tracks", id: Seed.looseTone)), title: "Loose Tone")
        XCTAssertTrue(player.exists)
    }

    /// Return on the row plays; the seeded files are 4 s long, so pause at once, seek by
    /// clicking the scrubber's middle, then resume and see the time move on.
    private func assertPlaysAndSeeks(_ app: XCUIApplication, row: XCUIElement, title: String) throws {
        let player = app.element("player.bar")
        row.click()
        app.typeKey(.return, modifierFlags: [])
        waitForText(title, in: player.element("player.title"))
        waitForText("Pause (Space)", in: player.element("player.playPause"))
        app.typeKey(" ", modifierFlags: [])
        waitForText("Play (Space)", in: player.element("player.playPause"))

        let elapsed = player.element("player.elapsed")
        let remaining = player.element("player.remaining")
        // The scale is known (4 s file): elapsed plus remaining.
        waitForText("−0:0", in: remaining)
        player.element("player.scrubber").coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).click()
        waitForText("0:02", in: elapsed)
        app.typeKey(" ", modifierFlags: [])
        waitForText("Pause (Space)", in: player.element("player.playPause"))
        waitForText("0:03", in: elapsed)
        app.typeKey(" ", modifierFlags: [])
    }
}
