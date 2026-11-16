import XCTest

/// The Radio view: open a show and an episode, play the episode from an entry, and see
/// the player move on and the current entry follow, in the bar and in the tracklist.
final class RadioFlowTests: EPUITestCase {
    func testPlayFromAnEntryAndTheCurrentEntryFollows() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")
        app.element("toolbar.section").radioButtons["Radio"].click()
        waitFor(app.row("shows", id: Seed.lateNightTapes)).click()
        waitFor(app.row("episodes", id: Seed.nightShift)).click()
        waitForText("Homage to the night shift", in: app.element("tracklist.header"))

        // Entry 1 (Jóga, 0:05–0:15) of the Ogg Opus episode: the decoding engine plays it.
        let first = waitFor(app.row("tracklist", id: Seed.nightShiftEntry1))
        first.doubleClick()
        waitForText("Jóga", in: player.element("player.title"))
        waitForText("Late Night Tapes", in: player.element("player.context"))
        waitForText("Pause (Space)", in: player.element("player.playPause"))
        waitForText("Now playing", in: first)

        // Ten seconds on, entry 2 (Loose Tone) plays: the bar and the tracklist follow.
        let second = app.row("tracklist", id: Seed.nightShiftEntry2)
        waitForText("Loose Tone", in: player.element("player.title"), timeout: 20)
        waitForText("0:1", in: player.element("player.elapsed"))
        waitForText("Now playing", in: second)

        // From the Library, Show playing (⌘L) comes back to the episode at that entry.
        app.element("toolbar.section").radioButtons["Library"].click()
        waitForAbsence(second)
        app.typeKey("l", modifierFlags: .command)
        waitFor(app.row("tracklist", id: Seed.nightShiftEntry2))
        app.typeKey(" ", modifierFlags: [])
        waitForText("Play (Space)", in: player.element("player.playPause"))
    }
}
