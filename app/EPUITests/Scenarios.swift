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

    /// Player: nothing played yet.
    func testPlayerNothing() throws {
        let app = try launchConnected()
        waitFor(app.element("player.nothingPlaying"))
        screenshot("playerNothing", of: app.element("player.bar"))
    }

    /// Player: Homogenic's Jóga from its own file, paused once it shows.
    func testPlayerPlaying() throws {
        let app = try launchConnected()
        play(Seed.jogaTrack, on: Seed.homogenic, in: app)
        waitForText("Jóga", in: app.element("player.title"))
        app.element("player.playPause").click()
        screenshot("playerPlaying")
    }

    /// Player: Cocoon, which plays only inside a radio episode: the bar grows, the
    /// scrubber covers the episode with hollow (estimated) marks.
    func testPlayerLongFile() throws {
        let app = try launchConnected()
        play(Seed.cocoonTrack, on: Seed.vespertine, in: app)
        waitForText("of 2", in: app.element("player.context"))
        app.element("player.playPause").click()
        screenshot("playerLongFile")
    }

    /// Player: Medúlla's track 2, a range of the album's one file.
    func testPlayerSingleFile() throws {
        let app = try launchConnected()
        play(Seed.medullaTrack2, on: Seed.medulla, in: app)
        waitForText("Track 2 of 3", in: app.element("player.context"))
        app.element("player.playPause").click()
        screenshot("playerSingleFile")
    }

    /// Player: tried to play Bachelorette, which is only a Bandcamp link.
    func testPlayerLinkOnly() throws {
        let app = try launchConnected()
        play(Seed.bacheloretteTrack, on: Seed.homogenic, in: app)
        waitFor(app.element("player.notice"))
        screenshot("playerLinkOnly")
    }

    /// Player: Hunter playing, the queue popover open (Jóga up next).
    func testPlayerQueue() throws {
        let app = try launchConnected()
        play(Seed.hunterTrack, on: Seed.homogenic, in: app)
        waitForText("Hunter", in: app.element("player.title"))
        app.element("player.playPause").click()
        app.element("player.queue").click()
        waitFor(app.element("player.queue.row.0"))
        screenshot("playerQueue")
    }

    /// Detail panels: Björk → Homogenic → Jóga, each panel with its note (links, Björk's
    /// broken one), tags and Linked from (backlinks, radio appearances).
    func testDetailPanels() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.jogaTrack)).click()
        waitFor(app.element("detail.track.note.editor"))
        waitFor(app.element("detail.album.note.editor"))
        waitFor(app.element("detail.artist.note.editor"))
        screenshot("detailPanels")
    }

    /// Mark Bell's note: headings, a paragraph and a list item with a link.
    func testNoteLinks() throws {
        let app = try launchConnected()
        waitFor(app.row("artists", id: Seed.markBell)).click()
        waitForText("Homogenic", in: app.element("detail.artist.note.editor"))
        screenshot("noteLinks", of: app.element("detail.artist"))
    }

    /// Björk's note close up: the link to The Sugarcubes is broken.
    func testNoteBrokenLink() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitForText("Sugarcubes", in: app.element("detail.artist.note.editor"))
        screenshot("noteBrokenLink", of: app.element("detail.artist"))
    }

    /// Typing "[[homgen" in a track's note: the link suggestions (typo tolerant).
    func testNoteAutocomplete() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.allIsFullTrack)).click()
        let editor = waitFor(app.element("detail.track.note.editor"))
        editor.click()
        editor.typeText("Pairs with [[homgen")
        waitFor(app.element("linkSuggestions.row.0"))
        screenshot("noteAutocomplete")
    }

    /// The note changed on the server while it was edited here: Keep Mine / Use Theirs.
    func testNoteConflict() async throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.bacheloretteTrack)).click()
        let editor = waitFor(app.element("detail.track.note.editor"))
        try await Harness.put(
            "notes/recording/\(Seed.bacheloretteRecording)", json: ["body": "Written in another window."])
        editor.click()
        editor.typeText("Mine")
        waitFor(app.element("detail.track.note.conflict"))
        screenshot("noteConflict")
    }

    /// The detail panels collapsed (⌥⌘D): only their headers stay.
    func testDetailCollapsed() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.element("detail.album.tags"))
        // The menu item (⌥⌘D): XCUITest's synthetic ⌥⌘D does not reach its key equivalent.
        app.menuBars.menuItems["Hide Detail Panels"].click()
        waitForAbsence(app.element("detail.album.tags"))
        screenshot("detailCollapsed")
    }

    /// The main window switched to Radio.
    func testRadio() throws {
        let app = try launchConnected()
        app.element("toolbar.section").radioButtons["Radio"].click()
        waitFor(app.element("shows.column"))
        screenshot("radio")
    }

    /// Radio: Late Night Tapes → "Homage to the night shift" with its tracklist (matched,
    /// radio-only and unmatched entries), the second entry selected.
    func testRadioEpisode() throws {
        let app = try launchConnected()
        openNightShift(in: app)
        // On the time: the artist and title of a matched entry are links into the Library.
        waitFor(app.row("tracklist", id: Seed.nightShiftEntry2))
            .coordinate(withNormalizedOffset: CGVector(dx: 0.04, dy: 0.5)).click()
        screenshot("radioEpisode")
    }

    /// Radio: the episode's panel on its "Linked from" tab (Mark Bell's note links here).
    func testRadioPanel() throws {
        let app = try launchConnected()
        openNightShift(in: app)
        waitFor(app.element("detail.radio.tab.linkedFrom")).click()
        waitFor(app.element("detail.episode.linkedFrom"))
        screenshot("radioPanel")
    }

    /// The search palette (⌘K) with grouped results for "bjork".
    func testSearchResults() throws {
        let app = try launchConnected()
        app.typeKey("k", modifierFlags: .command)
        let field = waitFor(app.element("search.field"))
        field.typeText("bjork vesp")
        waitFor(app.element("search.group.topHit"))
        waitFor(app.element("search.row.0"))
        screenshot("searchResults")
    }

    /// A tag click (Homogenic's #strings): the palette filtered to the tag.
    func testSearchTag() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.element("detail.album.tag.\(Seed.stringsTag)")).click()
        waitFor(app.element("search.tag"))
        waitFor(app.element("search.row.0"))
        screenshot("searchTag")
    }

    /// The sources popover of Unravel, whose file is missing (Locate…).
    func testSourcesPopover() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.unravelTrack)).rightClick()
        app.menuItems["Sources…"].firstMatch.click()
        waitFor(app.element("sources.popover"))
        screenshot("sourcesPopover")
    }

    /// The album edit sheet (⌘E on Homogenic).
    func testAlbumEditSheet() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        app.typeKey("e", modifierFlags: .command)
        waitFor(app.element("edit.album.title"))
        screenshot("albumEditSheet")
    }

    /// The artist edit sheet (⌘E on Björk).
    func testArtistEditSheet() throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        app.typeKey("e", modifierFlags: .command)
        waitFor(app.element("edit.artist.name"))
        screenshot("artistEditSheet")
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

    /// Radio → Late Night Tapes → "Homage to the night shift".
    private func openNightShift(in app: XCUIApplication) {
        app.element("toolbar.section").radioButtons["Radio"].click()
        waitFor(app.row("shows", id: Seed.lateNightTapes)).click()
        waitFor(app.row("episodes", id: Seed.nightShift)).click()
        waitFor(app.row("tracklist", id: Seed.nightShiftEntry1))
    }

    /// Björk → `album`, then double-clicks the track row.
    private func play(_ track: String, on album: String, in app: XCUIApplication) {
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: album)).click()
        waitFor(app.row("tracks", id: track)).doubleClick()
    }
}
