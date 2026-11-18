import EPAPI
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

// Whether a row's entity has a note. The API's list responses (`ArtistRead`,
// `ReleaseGroupRead`, `RecordingRead`, `PlayableRecordingRead`) do not say so yet, so no
// row shows the icon. Once the server adds a `has_note` field and the client is
// regenerated, these computed properties clash with the generated ones: delete them.

extension Components.Schemas.ArtistRead {
    var hasNote: Bool { false }
}

extension Components.Schemas.ReleaseGroupRead {
    var hasNote: Bool { false }
}

extension Components.Schemas.RecordingRead {
    var hasNote: Bool { false }
}

extension Components.Schemas.PlayableRecordingRead {
    var hasNote: Bool { false }
}

extension Components.Schemas.RecordingDetail {
    var hasNote: Bool { false }
}
