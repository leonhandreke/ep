import AppKit

/// Launch arguments for one run, read from UserDefaults' argument domain and never saved.
/// The UI test harness uses them for deterministic screenshots; the server address
/// override lives in `AppSettings`.
///
/// - `-windowSize 1280x800`: the main window's size when it has no saved frame.
/// - `-appearance light|dark`: the app's appearance instead of the system's.
enum LaunchOptions {
    static var windowSize: CGSize {
        UserDefaults.standard.string(forKey: "windowSize").flatMap(parseSize) ?? WindowMetrics.defaultSize
    }

    static func applyAppearance() {
        switch UserDefaults.standard.string(forKey: "appearance") {
        case "light": NSApplication.shared.appearance = NSAppearance(named: .aqua)
        case "dark": NSApplication.shared.appearance = NSAppearance(named: .darkAqua)
        default: break
        }
    }

    /// "1280x800" → 1280 × 800; nil unless both are positive numbers.
    static func parseSize(_ text: String) -> CGSize? {
        let parts = text.lowercased().split(separator: "x").compactMap { Double($0) }
        guard parts.count == 2, parts.allSatisfy({ $0 > 0 }) else { return nil }
        return CGSize(width: parts[0], height: parts[1])
    }
}
