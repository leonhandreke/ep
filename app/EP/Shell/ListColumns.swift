import EPAPI
import SwiftUI

/// The three list columns (Library: artists, release groups, tracks — §3.2–3.4; Radio:
/// shows, episodes, tracklist — §6).
struct ListColumns: View {
    let section: AppSection

    var body: some View {
        switch section {
        case .library:
            LibraryColumns()
        case .radio:
            RadioColumns()
        }
    }
}

/// The Library's three columns. ← and → move the focus between them; moving right into
/// a column with nothing selected selects its first row.
private struct LibraryColumns: View {
    @Environment(LibraryBrowser.self) private var browser
    @FocusState private var focus: LibraryColumn?

    var body: some View {
        ColumnGrid(spec: .library) {
            ListColumn(id: "artists", label: "Artists", background: Palette.side) {
                ArtistsColumn(focus: $focus) { moveRight(from: .artists) }
            }
            ListColumn(id: "releaseGroups", label: "Albums") {
                ReleaseGroupsColumn(focus: $focus, moveLeft: { focus = .artists }) { moveRight(from: .releaseGroups) }
            }
            ListColumn(id: "tracks", label: "Tracks", trailingBorder: false) {
                TracksColumn(focus: $focus) { focus = .releaseGroups }
            }
        }
    }

    private func moveRight(from column: LibraryColumn) {
        switch column {
        case .artists:
            guard browser.location.artistID != nil, browser.artist != nil else { return }
            if browser.location.album == nil {
                if let first = browser.releaseGroups.first {
                    browser.select(album: .releaseGroup(first.id))
                } else if (browser.artist?.counts.looseRecordings ?? 0) > 0 {
                    browser.select(album: .looseTracks)
                } else {
                    return
                }
            }
            focus = .releaseGroups
        case .releaseGroups:
            guard browser.location.album != nil, let first = browser.tracks.first else { return }
            if browser.location.trackID == nil { browser.select(track: first.id) }
            focus = .tracks
        case .tracks:
            break
        }
    }
}

/// One list column: content and a 1 pt right border.
struct ListColumn<Content: View>: View {
    let id: String
    let label: LocalizedStringKey
    var background = Palette.bg
    var trailingBorder = true
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) { content }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .background(background)
            .overlay(alignment: .trailing) {
                if trailingBorder { HairlineDivider(axis: .vertical) }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(label)
            .accessibilityIdentifier("\(id).column")
    }
}

/// A 1 pt `--line` separator.
struct HairlineDivider: View {
    var axis: Axis = .horizontal

    var body: some View {
        Rectangle()
            .fill(Palette.line)
            .frame(
                width: axis == .vertical ? Metrics.hairline : nil,
                height: axis == .horizontal ? Metrics.hairline : nil
            )
            .accessibilityHidden(true)
    }
}
