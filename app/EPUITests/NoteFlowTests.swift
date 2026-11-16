import XCTest

/// Writing a note: a link through the `[[` suggestions, autosave, the stored form on the
/// server, then ⌘-click on the link and back.
final class NoteFlowTests: EPUITestCase {
    func testLinkThroughSuggestionsSaveAndFollow() async throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.homogenic)).click()
        waitFor(app.row("tracks", id: Seed.unravelTrack)).click()
        waitForText("Unravel", in: app.element("detail.track.title"))

        // "[[Mark Be" → the suggestions → Return inserts the link; typing goes on after it.
        let editor = waitFor(app.element("detail.track.note.editor"))
        editor.click()
        editor.typeText("[[Mark Be")
        waitForText("Mark Bell", in: app.element("linkSuggestions.row.0"))
        editor.typeText("\n")
        waitForAbsence(app.element("linkSuggestions"))
        editor.typeText(" produced it.")
        waitForText("Saved", in: app.element("detail.track.note.status"), timeout: 10)

        // Stored as typed, with the link in its stored form and resolved to Mark Bell.
        let stored = try await Harness.get("notes/recording/\(Seed.unravelRecording)")
        let note = try XCTUnwrap(stored as? [String: Any])
        let body = try XCTUnwrap(note["body"] as? String)
        XCTAssertTrue(body.hasPrefix("[[artist:"), body)
        XCTAssertTrue(body.hasSuffix("|Mark Bell]] produced it."), body)
        let links = try XCTUnwrap(note["links"] as? [[String: Any]])
        XCTAssertEqual(links.count, 1)
        let target = try XCTUnwrap(links.first?["target"] as? [String: Any])
        XCTAssertEqual(target["id"] as? String, Seed.markBell)
        let range = try XCTUnwrap(links.first?["range"] as? [String: Int])
        XCTAssertEqual(range["start"], 0)

        // ⌘-click on the link (it starts the note) shows Mark Bell; Back returns.
        let link = editor.coordinate(withNormalizedOffset: .zero).withOffset(CGVector(dx: 20, dy: 9))
        XCUIElement.perform(withKeyModifiers: .command) { link.click() }
        waitForText("Mark Bell", in: app.element("detail.artist.title"))
        app.typeKey("[", modifierFlags: .command)
        waitForText("Unravel", in: app.element("detail.track.title"))
        waitForText("Björk", in: app.element("detail.artist.title"))
    }
}
