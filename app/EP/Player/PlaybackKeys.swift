import AppKit

/// Space plays and pauses (§12), except while typing: a key-down monitor that leaves the
/// key to text views (SwiftUI's text fields edit through one). A menu key equivalent
/// would take Space from text fields too.
enum PlaybackKeys {
    static func install(_ player: Player) {
        NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
            handles(event, player: player) ? nil : event
        }
    }

    private static func handles(_ event: NSEvent, player: Player) -> Bool {
        let space: UInt16 = 49
        guard event.keyCode == space,
            event.modifierFlags.intersection([.command, .option, .control, .shift]).isEmpty,
            !(event.window?.firstResponder is NSText),
            player.current != nil
        else { return false }
        if !event.isARepeat { player.togglePlayPause() }
        return true
    }
}
