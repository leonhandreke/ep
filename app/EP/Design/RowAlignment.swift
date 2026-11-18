import AppKit
import SwiftUI

// One rule for the vertical alignment of every list and table row (and of headers that
// set texts of different sizes side by side):
//
// - Text cells share one baseline: a row's texts sit in a `TextRow` (an `HStack` on
//   `.firstTextBaseline`), so a 13 pt title, 12 pt meta and 11 pt times read as one line
//   of type. Centring them instead (`HStack`'s default) puts smaller text higher.
// - Anything else in such a row (icons, small buttons, the play-state icon) is centred
//   on the text's capitals with `centeredOnText(size:)`.
// - Cells that are not one line of text (covers, two-line title blocks, chips, controls)
//   are centred on the row: an ordinary `HStack`, with the `TextRow` inside it.
// - A `ColumnGrid` centres its cells vertically and reports its first cell's baseline,
//   so a grid of text cells takes part in a `TextRow` like any text.

/// A row's text cells on one baseline (see above).
struct TextRow<Content: View>: View {
    var spacing: CGFloat? = nil
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: spacing) { content }
    }
}

extension View {
    /// Places a non-text view (icon, small button) in a `TextRow` centred on the capitals
    /// of the row's `size` pt text, rather than standing on the baseline.
    func centeredOnText(size: CGFloat = 13) -> some View {
        let capHeight = TextMetrics.capHeight(size)
        return alignmentGuide(.firstTextBaseline) { $0[VerticalAlignment.center] + capHeight / 2 }
    }
}

enum TextMetrics {
    /// The system font's cap height at `size` points.
    static func capHeight(_ size: CGFloat) -> CGFloat {
        NSFont.systemFont(ofSize: size).capHeight
    }
}
