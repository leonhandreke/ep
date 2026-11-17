import XCTest

/// Import review by keyboard: open the queue (⇧⌘I), review the NTS episode, keep the
/// preselected "same recording", accept the next question, make Arca a new artist, and
/// confirm (⌘↩); the server queues the job to run again with the answers.
final class ImportFlowTests: EPUITestCase {
    func testResolveAReviewByKeyboard() async throws {
        let app = try launchConnected()
        app.typeKey("i", modifierFlags: [.command, .shift])
        waitFor(app.element("import.job.\(Seed.turiyaJob).review")).click()
        let first = waitFor(app.element("review.row.uncertain_recording:\(Seed.turiyaURL)#1"))
        waitForText("Same recording", in: first)

        app.typeKey("1", modifierFlags: [])  // the preselected candidate
        app.typeKey(.return, modifierFlags: [])  // accept; next: the entry with no title
        app.typeKey(.return, modifierFlags: [])  // accept its default; next: which Arca?
        waitForText("Which artist", in: app.element("review.question"))
        app.typeKey("n", modifierFlags: [])
        waitForText("New artist", in: app.element("review.row.uncertain_artist:Arca"))
        app.typeKey(.return, modifierFlags: .command)
        waitForAbsence(app.element("review.sheet"))

        // The answers went with the review: the job is queued to run again with them
        // (the server keeps them internally; no worker runs in the harness).
        let read = try await Harness.get("imports/\(Seed.turiyaJob)")
        let job = try XCTUnwrap(read as? [String: Any])
        XCTAssertEqual(job["state"] as? String, "queued")
        XCTAssertEqual(job["actions"] as? [String], ["cancel"])
    }
}
