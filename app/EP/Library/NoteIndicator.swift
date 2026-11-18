import SwiftUI

/// The note icon on a row whose artist, album or track (its recording) has a note: at the
/// trailing end of artist and album rows, after the title in track rows.
struct NoteIndicator: View {
    /// On a focused selection everything is `--seltext`.
    var emphasized = false

    var body: some View {
        Image(systemName: "note.text")
            .font(.system(size: 11))
            .foregroundStyle(RowForeground(emphasized: emphasized).tertiary)
            .help("Has a note")
            .accessibilityLabel("Has a note")
    }
}
