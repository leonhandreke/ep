import AppKit
import XCTest

/// The palette's own shortcuts beat the menu bar's: ⌘C copies the hit's link (not Edit ▸
/// Copy), ⌘↩ plays it, ⌘F opens the palette.
final class SearchFlowTests: EPUITestCase {
    func testCopyLinkAndPlayBeatTheMenuBar() throws {
        let app = try launchConnected()
        let player = app.element("player.bar")

        app.typeKey("f", modifierFlags: .command)
        let field = waitFor(app.element("search.field"))
        field.typeText("homogenic")
        waitFor(app.element("search.row.0"))
        NSPasteboard.general.clearContents()
        app.typeKey("c", modifierFlags: .command)
        waitForAbsence(app.element("search.palette"))
        XCTAssertTrue(
            NSPasteboard.general.string(forType: .string)?.hasPrefix("[[release_group:") == true,
            "⌘C copied: \(NSPasteboard.general.string(forType: .string) ?? "nothing")")

        app.typeKey("k", modifierFlags: .command)
        waitFor(app.element("search.field")).typeText("homogenic")
        waitFor(app.element("search.row.0"))
        app.typeKey(.return, modifierFlags: .command)
        waitForAbsence(app.element("search.palette"))
        waitForText("Hunter", in: player.element("player.title"))
    }
}
