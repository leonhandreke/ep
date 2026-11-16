import XCTest

/// Editing: rename an album in its sheet (⌘E) and see the change reach the server and
/// the Library. The title is put back afterwards for the other tests of the run.
final class EditFlowTests: EPUITestCase {
    func testRenameAnAlbumInTheSheet() async throws {
        let app = try launchConnected()
        app.row("artists", id: Seed.bjork).click()
        waitFor(app.row("releaseGroups", id: Seed.medulla)).click()
        app.typeKey("e", modifierFlags: .command)

        let title = waitFor(app.element("edit.album.title"))
        waitForText("Medúlla", in: title)
        title.click()
        title.typeKey("a", modifierFlags: .command)
        title.typeText("Medúlla (renamed)")
        app.element("edit.album.save").click()
        waitForAbsence(app.element("edit.album.title"))

        let group = try await Harness.get("release-groups/\(Seed.medulla)") as? [String: Any]
        XCTAssertEqual(group?["title"] as? String, "Medúlla (renamed)")
        waitForText("Medúlla (renamed)", in: app.row("releaseGroups", id: Seed.medulla))

        try await Harness.patch("release-groups/\(Seed.medulla)", json: ["title": "Medúlla"])
    }
}
