import EPAPI
import SwiftUI

/// Re-points a tracklist entry: search the library's recordings (typo-tolerant, from
/// the server) and choose the one the entry played. Unmatching is the context menu's
/// "Unmatch".
struct RepointSheet: View {
    let entry: Components.Schemas.RadioEpisodeTrackRead
    let choose: (String) -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var search: SearchStore?
    @State private var text = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Match “\(entry.artistText) — \(entry.titleText)”")
                .font(Typography.panelTitle)
            Text("Choose the library recording this entry played. The entry’s excerpt moves with it.")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text2)
            TextField("Search recordings", text: $text)
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .onChange(of: text) { _, new in search?.update(new) }
                .onKeyPress(.downArrow) {
                    search?.move(1)
                    return .handled
                }
                .onKeyPress(.upArrow) {
                    search?.move(-1)
                    return .handled
                }
                .onSubmit(chooseSelected)
                .accessibilityLabel("Search recordings")
                .accessibilityIdentifier("repoint.search")
            ScrollView {
                VStack(spacing: 0) {
                    if let search {
                        ForEach(Array(search.hits.enumerated()), id: \.element.id) { index, hit in
                            EntityHitRow(hit: hit, selected: index == search.selection, id: "repoint.row.\(index)")
                                .background(
                                    index == search.selection ? Palette.sel : .clear,
                                    in: RoundedRectangle(cornerRadius: Radius.rowLarge)
                                )
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    search.select(hit)
                                    chooseSelected()
                                }
                        }
                        if search.hits.isEmpty, !search.isEmpty, !search.isSearching {
                            Text("No recordings match.")
                                .font(Typography.secondary)
                                .foregroundStyle(Palette.text3)
                                .padding(.top, 8)
                        }
                    }
                }
            }
            .frame(height: 280)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                    .accessibilityIdentifier("repoint.cancel")
                Button("Match", action: chooseSelected)
                    .keyboardShortcut(.defaultAction)
                    .disabled(search?.selected == nil)
                    .accessibilityIdentifier("repoint.match")
            }
        }
        .padding(20)
        .frame(width: 480)
        .onAppear {
            let store = SearchStore(connection: connection, types: [.recording], grouped: false)
            search = store
            text = [entry.artistText, entry.titleText].joined(separator: " ")
            store.update(text)
            focused = true
        }
        .accessibilityIdentifier("repoint.sheet")
    }

    private func chooseSelected() {
        guard let hit = search?.selected else { return }
        choose(hit.ref.id)
        dismiss()
    }
}
