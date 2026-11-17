import EPAPI
import SwiftUI

/// The release-groups column (§3.3): "Artist · N releases · by date" (a radio-only artist:
/// "Artist · known from radio only", §11.4), the artist's albums oldest first with cover,
/// title and "1997 · Album", then the "Loose tracks" entry, then "Appears on": albums by
/// others with a track crediting the artist, with their artist ("Bonobo · 2017 · Album").
/// An artist with none of these gets the "no releases" state (§11.4).
struct ReleaseGroupsColumn: View {
    var focus: FocusState<LibraryColumn?>.Binding
    let moveLeft: () -> Void
    let moveRight: () -> Void

    @Environment(LibraryBrowser.self) private var browser
    @Environment(\.presentSheet) private var presentSheet

    var body: some View {
        VStack(spacing: 0) {
            ColumnHeader(
                id: "releaseGroups", title: browser.artist?.name, meta: meta,
                metaPlacement: browser.artist?.radioOnly == true ? .joined : .trailing
            ) { EmptyView() }
            content
        }
    }

    private var meta: String? {
        guard let artist = browser.artist else { return nil }
        if artist.radioOnly { return String(localized: "known from radio only") }
        let count = artist.counts.releaseGroups
        return count == 1
            ? String(localized: "1 release · by date")
            : String(localized: "\(LibraryFormat.count(count)) releases · by date")
    }

    @ViewBuilder private var content: some View {
        if browser.location.artistID == nil {
            Color.clear
        } else if let artist = browser.artist {
            if browser.releaseGroups.isEmpty && artist.counts.looseRecordings == 0 && browser.appearances.isEmpty {
                NoReleasesView(artist: artist)
            } else {
                list(artist: artist)
            }
        } else {
            switch browser.releaseGroupsPhase {
            case .failed(let message): ColumnMessage(id: "releaseGroups", text: message) { browser.reload() }
            default: DelayedSkeleton(rowHeight: RowHeight.releaseGroup, cover: true)
            }
        }
    }

    private func emphasized(_ album: AlbumSelection) -> Bool {
        focus.wrappedValue == .releaseGroups && browser.location.album == album
    }

    private func soft(_ album: AlbumSelection) -> Bool {
        focus.wrappedValue != .releaseGroups && browser.location.album == album
    }

    private func list(artist: Components.Schemas.ArtistDetail) -> some View {
        ScrollViewReader { proxy in
            List(
                selection: shownSelection(browser.location.album) {
                    browser.select(album: $0)
                }
            ) {
                ForEach(browser.releaseGroups, id: \.id) { group in
                    ReleaseGroupRow(group: group, emphasized: emphasized(.releaseGroup(group.id)))
                        .tag(AlbumSelection.releaseGroup(group.id))
                        .softSelection(soft(.releaseGroup(group.id)), radius: Radius.rowLarge)
                        .id(AlbumSelection.releaseGroup(group.id))
                        .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
                        .listRowSeparator(.hidden)
                }
                if artist.counts.looseRecordings > 0 {
                    LooseTracksRow(count: artist.counts.looseRecordings, emphasized: emphasized(.looseTracks))
                        .tag(AlbumSelection.looseTracks)
                        .softSelection(soft(.looseTracks), radius: Radius.rowLarge)
                        .id(AlbumSelection.looseTracks)
                        .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
                        .listRowSeparator(.hidden)
                }
                if !browser.appearances.isEmpty {
                    Section {
                        ForEach(browser.appearances, id: \.id) { group in
                            ReleaseGroupRow(group: group, emphasized: emphasized(.releaseGroup(group.id)), showsCredit: true)
                                .tag(AlbumSelection.releaseGroup(group.id))
                                .softSelection(soft(.releaseGroup(group.id)), radius: Radius.rowLarge)
                                .id(AlbumSelection.releaseGroup(group.id))
                                .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
                                .listRowSeparator(.hidden)
                        }
                    } header: {
                        Text("Appears on")
                            .sectionLabelStyle()
                            .padding(.top, 6)
                            .padding(.bottom, 4)
                            .padding(.leading, 2)
                            .accessibilityIdentifier("releaseGroups.appearsOn")
                    }
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.releaseGroup)
            .focused(focus, equals: .releaseGroups)
            .onKeyPress(.leftArrow) {
                moveLeft()
                return .handled
            }
            .onKeyPress(.rightArrow) {
                moveRight()
                return .handled
            }
            .contextMenu(forSelectionType: AlbumSelection.self) { selection in
                if let id = selection.first?.releaseGroupID,
                    let group = (browser.releaseGroups + browser.appearances).first(where: { $0.id == id })
                {
                    Button("Edit…") { presentSheet?(WindowSheet(kind: .edit(.releaseGroup(group.id)))) }
                    Button("Copy Link") { copyNoteLink(group.link) }
                }
            }
            .focusedValue(
                \.editSelection,
                browser.location.album?.releaseGroupID.map { id in
                    { presentSheet?(WindowSheet(kind: .edit(.releaseGroup(id)))) }
                }
            )
            .onChange(of: browser.location.album) { _, album in
                if let album { proxy.scrollTo(album) }
            }
            .accessibilityIdentifier("releaseGroups.list")
        }
    }
}

private struct ReleaseGroupRow: View {
    let group: Components.Schemas.ReleaseGroupRead
    let emphasized: Bool
    /// Leads the meta line with the album's artist (an "Appears on" album).
    var showsCredit = false

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        HStack(spacing: 10) {
            CoverThumbnail(path: group.coverUrl)
            VStack(alignment: .leading, spacing: 2) {
                Text(group.title)
                    .font(Typography.rowTitle)
                    .foregroundStyle(colors.primary)
                    .lineLimit(1)
                Text(
                    [showsCredit ? group.artistCredit.name : nil, LibraryFormat.year(group.date), group._type.displayName]
                        .compactMap(\.self)
                        .joined(separator: " · ")
                )
                .font(Typography.meta)
                .monospacedDigit()
                .foregroundStyle(colors.tertiary)
                .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .frame(height: RowHeight.releaseGroup)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("releaseGroups.row.\(group.id)")
    }
}

/// "Loose tracks · N recordings on no release": the dashed cover slot with a note.
private struct LooseTracksRow: View {
    let count: Int
    let emphasized: Bool

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: Radius.coverSmall)
                .strokeBorder(colors.tertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
                .frame(width: Metrics.coverSmall, height: Metrics.coverSmall)
                .overlay {
                    Image(systemName: "music.note.list")
                        .font(.system(size: 13))
                        .foregroundStyle(colors.tertiary)
                }
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                Text("Loose tracks")
                    .font(Typography.rowTitle)
                    .foregroundStyle(colors.primary)
                Text(
                    count == 1
                        ? String(localized: "1 recording on no release")
                        : String(localized: "\(LibraryFormat.count(count)) recordings on no release")
                )
                .font(Typography.meta)
                .foregroundStyle(colors.tertiary)
            }
            .lineLimit(1)
            Spacer(minLength: 0)
        }
        .frame(height: RowHeight.releaseGroup)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("releaseGroups.row.looseTracks")
    }
}

/// An artist with no albums and no loose tracks (§11.4): "No albums in your library.
/// Played in N radio episodes." with Add album by hand… and Import….
private struct NoReleasesView: View {
    let artist: Components.Schemas.ArtistDetail

    @Environment(\.presentSheet) private var presentSheet
    @Environment(\.openImport) private var openImport

    var body: some View {
        VStack(spacing: 12) {
            let episodes = artist.counts.radioEpisodes
            let played = Text(
                episodes == 1
                    ? String(localized: "1 radio episode")
                    : String(localized: "\(LibraryFormat.count(episodes)) radio episodes")
            ).foregroundStyle(Palette.accent)
            Group {
                if episodes > 0 {
                    Text("No albums in your library. Played in \(played).")
                } else {
                    Text("No albums in your library.")
                }
            }
            .font(Typography.body)
            .foregroundStyle(Palette.text2)
            .multilineTextAlignment(.center)
            .frame(maxWidth: 300)
            HStack(spacing: 8) {
                Button("Add album by hand…") {
                    let credit = CreditPart(artistID: artist.id, name: artist.name, joinPhrase: "")
                    presentSheet?(WindowSheet(kind: .create(kinds: [.album], name: "", credit: [credit])))
                }
                .accessibilityIdentifier("releaseGroups.addByHand")
                Button("Import…") { openImport?(nil) }
                    .disabled(openImport == nil)
                    .accessibilityIdentifier("releaseGroups.import")
            }
            .controlSize(.small)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("releaseGroups.empty")
    }
}
