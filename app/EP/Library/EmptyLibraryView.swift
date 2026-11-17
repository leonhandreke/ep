import AppKit
import SwiftUI

/// The empty library on first launch (§11.1): "Nothing here yet", with Import… and
/// Watch a folder…, and the ⌘V hint. Shown in place of the Library's columns and panels
/// while the server reports nothing in the library.
struct EmptyLibraryView: View {
    let importer: () -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(WatchFolders.self) private var folders

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "music.note")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(Palette.text3)
                .accessibilityHidden(true)
            Text("Nothing here yet")
                .font(Typography.emptyHeading)
                .foregroundStyle(Palette.text)
                .accessibilityAddTraits(.isHeader)
            Text("Bring music in once, or point EP at a folder and it will keep importing what lands there.")
                .font(Typography.body)
                .foregroundStyle(Palette.text2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 300)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button("Import…", action: importer)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .accessibilityIdentifier("empty.import")
                Button("Watch a folder…") {
                    WatchFolderPanel.run(folders: folders, connection: connection, window: NSApp.keyWindow)
                }
                .accessibilityIdentifier("empty.watchFolder")
            }
            .controlSize(.large)
            .padding(.top, 4)
            if let error = folders.error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 360)
                    .accessibilityIdentifier("empty.error")
            }
            Text("or paste a link anywhere with ⌘V")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("empty.library")
    }
}
