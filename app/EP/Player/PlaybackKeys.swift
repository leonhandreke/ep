import AppKit

/// Space plays and pauses (§12), except while typing: a key-down monitor that leaves the
/// key to text views (SwiftUI's text fields edit through one) and to sheets. A menu key equivalent
/// would take Space from text fields too.
enum PlaybackKeys {
    /// When the artist list last took a type-to-select key. Space typed within
    /// `Metrics.typeSelectTimeout` of it belongs to the typed text ("sigur r"), not to playback.
    private static var lastTypeSelect: ContinuousClock.Instant?

    static func noteTypeSelect() { lastTypeSelect = .now }

    private static var isTypeSelecting: Bool {
        lastTypeSelect.map { .now - $0 < Metrics.typeSelectTimeout } ?? false
    }

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
            // Sheets keep Space: the review sheet previews with it, buttons press with it.
            event.window?.sheetParent == nil,
            !isTypeSelecting,
            player.current != nil
        else { return false }
        if !event.isARepeat { player.togglePlayPause() }
        return true
    }
}
