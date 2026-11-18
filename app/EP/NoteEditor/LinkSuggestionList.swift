import SwiftUI

/// The `[[` link suggestions (board 2, listbox "Link suggestions"): width 420, radius 10,
/// rows of 46 pt (image, name, second line, type pill) or the no-match actions (36 pt),
/// then the key hints.
struct LinkSuggestionList: View {
    let model: LinkAutocomplete
    let choose: (Int) -> Void

    static let width: CGFloat = 420
    private static let footerHeight: CGFloat = 28
    private static let padding: CGFloat = 6

    /// The card's size for what the model shows.
    static func size(for model: LinkAutocomplete) -> CGSize {
        let rows = model.rows
        let body: CGFloat
        if rows.isEmpty {
            body = RowHeight.autocompleteAction
        } else if case .hit = rows[0] {
            body = CGFloat(rows.count) * RowHeight.searchHit
        } else {
            body = CGFloat(rows.count) * RowHeight.autocompleteAction
        }
        return CGSize(width: width, height: body + 2 * padding + footerHeight)
    }

    var body: some View {
        PopupCard {
            VStack(alignment: .leading, spacing: 0) {
                VStack(spacing: 0) {
                    let rows = model.rows
                    if rows.isEmpty {
                        Text(model.query?.text.isEmpty == false ? "Searching…" : "Type to search the library")
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.text3)
                            .padding(.horizontal, 10)
                            .frame(maxWidth: .infinity, minHeight: RowHeight.autocompleteAction, alignment: .leading)
                    }
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        rowView(row, index: index, selected: index == model.selection)
                            .contentShape(Rectangle())
                            .onTapGesture { choose(index) }
                    }
                }
                .padding(Self.padding)
                HStack(spacing: 14) {
                    Text("↑↓ choose")
                    Text("↩ insert link")
                    Text("⇥ insert & keep typing")
                    Text("esc close")
                }
                .font(Typography.meta)
                .foregroundStyle(Palette.text3)
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, minHeight: Self.footerHeight, alignment: .leading)
                .overlay(alignment: .top) { HairlineDivider() }
                .accessibilityHidden(true)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Link suggestions")
        .accessibilityIdentifier("linkSuggestions")
    }

    @ViewBuilder
    private func rowView(_ row: LinkAutocomplete.Row, index: Int, selected: Bool) -> some View {
        let foreground = RowForeground(emphasized: selected)
        Group {
            switch row {
            case .hit(let hit):
                EntityHitRow(hit: hit, selected: selected, id: "linkSuggestions.row.\(index)")
            case .newArtist(let name):
                action(
                    Label("New artist “\(name)”", systemImage: "plus"), key: "↩", id: "newArtist",
                    foreground: foreground)
            case .newOther:
                action(
                    Label("New album or recording…", systemImage: "plus"), key: "⌥↩", id: "newOther",
                    foreground: foreground)
            case .searchMusicBrainz:
                action(
                    Label("Search MusicBrainz", systemImage: "magnifyingglass"), key: "⇧↩", id: "searchMusicBrainz",
                    foreground: foreground)
            }
        }
        .background(selected ? Palette.sel : .clear, in: RoundedRectangle(cornerRadius: Radius.rowLarge))
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    private func action(_ label: Label<Text, Image>, key: String, id: String, foreground: RowForeground) -> some View {
        TextRow {
            label.font(Typography.body).foregroundStyle(foreground.primary)
            Spacer(minLength: 8)
            Text(key).font(Typography.meta).foregroundStyle(foreground.tertiary)
        }
        .padding(.horizontal, 10)
        .frame(height: RowHeight.autocompleteAction)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("linkSuggestions.\(id)")
    }
}

/// A mixed-type result row (46 pt, grid `36px 1fr auto`): image (round for artists,
/// shows and episodes), name over the second line, and the type pill. Link suggestions
/// and the search palette show the same rows.
struct EntityHitRow: View {
    let hit: EntitySummary
    let selected: Bool
    let id: String

    var body: some View {
        let foreground = RowForeground(emphasized: selected)
        HStack(spacing: 10) {
            if hit.roundImage {
                CoverThumbnail(path: hit.coverPath, size: 36, radius: 18)
            } else {
                CoverThumbnail(path: hit.coverPath, size: 36, radius: Radius.coverSmall)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(hit.title).font(Typography.name).foregroundStyle(foreground.primary).lineLimit(1)
                Text(hit.detail).font(Typography.secondary).foregroundStyle(foreground.secondary).lineLimit(1)
            }
            Spacer(minLength: 8)
            Text(hit.pill)
                .font(Typography.meta)
                .foregroundStyle(foreground.secondary)
                .padding(.horizontal, 5)
                .padding(.vertical, 1)
                .overlay(RoundedRectangle(cornerRadius: Radius.chip).strokeBorder(foreground.secondary))
        }
        .padding(.horizontal, 6)
        .frame(height: RowHeight.searchHit)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(hit.title), \(hit.detail), \(hit.pill)")
        .accessibilityIdentifier(id)
    }
}
