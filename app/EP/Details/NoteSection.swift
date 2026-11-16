import SwiftUI

/// NOTE: the entity's note (read-only rendering until the editor lands).
struct NoteSection: View {
    let details: EntityDetails
    let id: String

    var body: some View {
        DetailSection(title: String(localized: "Note"), id: "\(id).note") {
            if let note = details.note {
                Text(note.text)
                    .font(Typography.noteBody)
                    .lineSpacing(Typography.noteLineSpacing)
                    .foregroundStyle(Palette.text)
                    .textSelection(.enabled)
                    .frame(maxWidth: 720, alignment: .leading)
            }
        }
    }
}
