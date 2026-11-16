import EPAPI
import SwiftUI

/// NOTE: the entity's note in the live-preview editor, with the save status at the right
/// of the section label ("Editing…", board 2) and, after a 409, the conflict banner.
struct NoteSection: View {
    let details: EntityDetails
    let id: String

    @Environment(ServerConnection.self) private var connection

    var body: some View {
        DetailSection(title: String(localized: "Note"), id: "\(id).note") {
            if let note = details.note {
                SaveStatusText(document: note, readOnly: !connection.isConnected)
                    .accessibilityIdentifier("\(id).note.status")
            }
        } content: {
            if let note = details.note {
                if case .conflict(let current) = note.status {
                    ConflictBanner(theirs: current.body, id: "\(id).note.conflict") {
                        note.keepMine()
                    } takeTheirs: {
                        note.takeTheirs()
                    }
                }
                NoteEditor(document: note, id: id)
            }
        }
    }
}

/// "Editing…", "Saving…", "Saved", why a save failed, or that notes are read-only.
private struct SaveStatusText: View {
    let document: NoteDocument
    let readOnly: Bool

    var body: some View {
        let (text, color) = describe()
        Text(text)
            .font(Typography.secondary)
            .foregroundStyle(color)
            .lineLimit(1)
    }

    private func describe() -> (String, Color) {
        if readOnly {
            let suffix = document.hasUnsavedChanges ? String(localized: " · not saved yet") : ""
            return (String(localized: "Read-only until reconnected") + suffix, Palette.text3)
        }
        switch document.status {
        case .idle: return ("", Palette.text3)
        case .editing: return (String(localized: "Editing…"), Palette.text3)
        case .saving: return (String(localized: "Saving…"), Palette.text3)
        case .saved: return (String(localized: "Saved"), Palette.text3)
        case .failed(let message): return (message, Palette.danger)
        case .conflict: return (String(localized: "Changed elsewhere"), Palette.warn)
        }
    }
}

/// The note changed elsewhere since this edit started (409): keep this version (save it
/// over theirs) or use theirs (drop this edit). Theirs can be read first.
private struct ConflictBanner: View {
    let theirs: String
    let id: String
    let keepMine: () -> Void
    let takeTheirs: () -> Void

    @State private var showTheirs = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle")
                    .foregroundStyle(Palette.warn)
                    .accessibilityHidden(true)
                Text("This note was changed elsewhere while you edited it.")
                    .font(Typography.body)
                    .foregroundStyle(Palette.text)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            HStack(spacing: 8) {
                Button("Keep Mine", action: keepMine)
                    .accessibilityHint("Saves your version over the other one")
                    .accessibilityIdentifier("\(id).keepMine")
                Button("Use Theirs", action: takeTheirs)
                    .accessibilityHint("Drops your edit and shows the other version")
                    .accessibilityIdentifier("\(id).useTheirs")
                Button(showTheirs ? "Hide Theirs" : "Show Theirs") { showTheirs.toggle() }
                    .buttonStyle(.link)
                    .accessibilityIdentifier("\(id).showTheirs")
            }
            .controlSize(.small)
            if showTheirs {
                ScrollView {
                    Text(theirs.isEmpty ? String(localized: "(empty)") : theirs)
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text2)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 120)
            }
        }
        .padding(10)
        .background(Palette.chip, in: RoundedRectangle(cornerRadius: Radius.largeButton))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(id)
    }
}
