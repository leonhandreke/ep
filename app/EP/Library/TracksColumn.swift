import EPAPI
import SwiftUI

/// The tracks column (§3.4): album title and meta with Play album, disc headings, and
/// 26 pt rows on the grid `22 26 1fr 160 46` (play state, number, title, note, length).
/// An album with nothing that plays gets the banner of §11.5. Double-click or Return
/// plays the track through `LibraryBrowser.play(_:)` (stage 4).
struct TracksColumn: View {
    var focus: FocusState<LibraryColumn?>.Binding
    let moveLeft: () -> Void

    @Environment(LibraryBrowser.self) private var browser
    @Environment(Player.self) private var player
    /// The row whose sources popover is open (its state icon, or "Sources…").
    @State private var sourcesRowID: String?
    @Environment(\.presentSheet) private var presentSheet
    @Environment(\.openImport) private var openImport

    var body: some View {
        VStack(spacing: 0) {
            header
            if let group = browser.releaseGroup, browser.location.album == .releaseGroup(group.id),
                let banner = NoPlayableBanner.text(for: group)
            {
                // Import… starts from the first track's link (the server says whether
                // it imports it).
                NoPlayableBanner(text: banner) {
                    openImport?(browser.tracks.lazy.compactMap { $0.playability.link?.url }.first)
                }
            }
            content
        }
    }

    // MARK: - Header

    @ViewBuilder private var header: some View {
        switch browser.location.album {
        case .releaseGroup(let id):
            let group = browser.releaseGroup.flatMap { $0.id == id ? $0 : nil }
            ColumnHeader(
                id: "tracks", title: group?.title, meta: group.map(Self.meta), horizontalPadding: 14,
                metaPlacement: .afterTitle
            ) {
                Button("Play album") { browser.play(.album(id)) }
                    .buttonStyle(SmallBorderedButtonStyle())
                    .disabled(group == nil || group?.trackCount == 0)
                    .accessibilityLabel("Play album")
                    .accessibilityIdentifier("tracks.playAlbum")
            }
        case .looseTracks:
            let count = browser.artist?.counts.looseRecordings
            ColumnHeader(
                id: "tracks", title: String(localized: "Loose tracks"),
                meta: count.map {
                    $0 == 1
                        ? String(localized: "1 recording on no release")
                        : String(localized: "\(LibraryFormat.count($0)) recordings on no release")
                },
                horizontalPadding: 14, metaPlacement: .afterTitle
            ) { EmptyView() }
        case nil:
            ColumnHeader(id: "tracks", title: nil, meta: nil)
        }
    }

    /// "1997 · Album · 10 tracks · 43:35".
    static func meta(_ group: Components.Schemas.ReleaseGroupDetail) -> String {
        let tracks =
            group.trackCount == 1
            ? String(localized: "1 track") : String(localized: "\(LibraryFormat.count(group.trackCount)) tracks")
        return [
            LibraryFormat.year(group.date), group._type.displayName, tracks,
            group.lengthMs > 0 ? LibraryFormat.duration(ms: group.lengthMs) : nil,
        ]
        .compactMap(\.self).joined(separator: " · ")
    }

    // MARK: - Rows

    @ViewBuilder private var content: some View {
        if browser.location.album == nil {
            Color.clear
        } else if browser.tracks.isEmpty {
            switch browser.tracksPhase {
            case .loading, .idle: DelayedSkeleton(rowHeight: RowHeight.track)
            case .failed(let message): ColumnMessage(id: "tracks", text: message) { browser.reload() }
            case .loaded: ColumnMessage(id: "tracks", text: String(localized: "No tracks."))
            }
        } else {
            trackList
        }
    }

    private var discs: [(disc: Int, rows: [TrackRow])] {
        Dictionary(grouping: browser.tracks, by: \.disc).sorted { $0.key < $1.key }.map { ($0.key, $0.value) }
    }

    private var trackList: some View {
        ScrollViewReader { proxy in
            List(
                selection: shownSelection(browser.location.trackID) {
                    browser.select(track: $0)
                }
            ) {
                if (browser.releaseGroup?.discCount ?? 1) > 1 {
                    ForEach(discs, id: \.disc) { disc in
                        Section {
                            rows(disc.rows)
                        } header: {
                            Text("Disc \(disc.disc)")
                                .sectionLabelStyle()
                                .tracking(0.44)
                                .padding(.top, 6)
                                .padding(.bottom, 4)
                                .padding(.leading, 2)
                                .accessibilityIdentifier("tracks.disc.\(disc.disc)")
                        }
                    }
                } else {
                    rows(browser.tracks)
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.track)
            .focused(focus, equals: .tracks)
            .onKeyPress(.leftArrow) {
                moveLeft()
                return .handled
            }
            // ⌥↩ on a link-only track offers Import (§3.5): the import sheet with its link.
            .onKeyPress(.return, phases: .down) { press in
                guard press.modifiers == .option, let row = browser.selectedTrack, row.playState == .linkOnly,
                    let link = row.playability.link?.url, let openImport
                else { return .ignored }
                openImport(link)
                return .handled
            }
            .contextMenu(forSelectionType: String.self) { ids in
                if let row = ids.first.flatMap(row(id:)) {
                    Button("Edit…") { presentSheet?(WindowSheet(kind: .edit(.recording(row.recordingID)))) }
                    Button("Sources…") { sourcesRowID = row.id }
                    Divider()
                    Button("Copy Link") { copyNoteLink(row.link) }
                }
            } primaryAction: { ids in
                if let row = ids.first.flatMap(row(id:)) {
                    browser.play(.track(row, in: browser.tracks, album: browser.releaseGroup))
                }
            }
            .onChange(of: browser.location.trackID) { _, id in
                if let id { proxy.scrollTo(id) }
            }
            .focusedValue(
                \.editSelection,
                browser.selectedTrack.map { row in
                    { presentSheet?(WindowSheet(kind: .edit(.recording(row.recordingID)))) }
                }
            )
            .accessibilityIdentifier("tracks.list")
        }
    }

    private func rows(_ rows: [TrackRow]) -> some View {
        ForEach(rows) { row in
            TrackRowView(
                row: row, emphasized: focus.wrappedValue == .tracks && row.id == browser.location.trackID,
                isPlaying: player.isPlaying(row),
                showsSources: Binding(
                    get: { sourcesRowID == row.id }, set: { sourcesRowID = $0 ? row.id : nil })
            )
            .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
            .listRowSeparator(.hidden)
            .softSelection(focus.wrappedValue != .tracks && row.id == browser.location.trackID)
        }
    }

    private func row(id: String) -> TrackRow? { browser.tracks.first { $0.id == id } }
}

private struct TrackRowView: View {
    let row: TrackRow
    let emphasized: Bool
    /// The now-playing bars replace the number.
    let isPlaying: Bool
    @Binding var showsSources: Bool

    @Environment(ServerConnection.self) private var connection
    @Environment(LibraryBrowser.self) private var browser

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        TextRow(spacing: 0) {
            Button {
                showsSources = true
            } label: {
                PlayStateIcon(state: row.playState, emphasized: emphasized)
                    .frame(width: Metrics.trackIconColumn, height: RowHeight.track)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(String(localized: "\(row.playState.label) — click for sources"))
            .accessibilityLabel("Sources of \(row.title): \(row.playState.label)")
            .accessibilityIdentifier("tracks.row.\(row.id).sources")
            .popover(isPresented: $showsSources, arrowEdge: .trailing) {
                SourcesPopover(title: row.title, store: makeSources())
            }
            .centeredOnText()
            TextRow(spacing: 0) {
                Group {
                    if isPlaying {
                        Image(systemName: "chart.bar.fill")
                            .font(.system(size: 10))
                            .centeredOnText()
                            .accessibilityLabel("Now playing")
                            .accessibilityIdentifier("tracks.nowPlaying")
                    } else {
                        Text(row.number.map(String.init) ?? "")
                            .font(Typography.body)
                            .monospacedDigit()
                    }
                }
                .foregroundStyle(colors.tertiary)
                .frame(width: Metrics.trackNumberColumn - 6, alignment: .trailing)
                .padding(.trailing, 6)
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(row.title)
                        .font(Typography.body)
                        .italic(row.isItalic)
                        .foregroundStyle(row.isDimmed ? colors.tertiary : colors.primary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Group {
                    if let note = row.note {
                        Text(note.text)
                            .font(note.isCredit ? Typography.secondary : Typography.meta)
                            .foregroundStyle(
                                emphasized
                                    ? Palette.selText
                                    : note.isDanger ? Palette.danger : note.isCredit ? Palette.text2 : Palette.text3
                            )
                            .lineLimit(1)
                    } else {
                        Text(verbatim: " ").font(Typography.meta).accessibilityHidden(true)
                    }
                }
                .frame(width: Metrics.trackNoteColumn, alignment: .leading)
                .padding(.leading, 8)
                Text(LibraryFormat.duration(ms: row.lengthMS) ?? "")
                    .font(Typography.meta)
                    .monospacedDigit()
                    .foregroundStyle(colors.tertiary)
                    .frame(width: Metrics.trackLengthColumn, alignment: .trailing)
            }
        }
        .padding(.horizontal, 6)
        .frame(height: RowHeight.track)
        .contentShape(Rectangle())
        .help(row.playState.label)
        .accessibilityElement(children: .combine)
        .accessibilityValue(isPlaying ? String(localized: "Now playing") : "")
        .accessibilityIdentifier("tracks.row.\(row.id)")
    }

    private func makeSources() -> SourcesStore {
        let store = SourcesStore(recordingID: row.recordingID, connection: connection)
        store.onChange = { [weak browser] in browser?.reload() }
        return store
    }
}

/// "None of these 8 tracks play here. 6 have a Spotify link." with Import… (§11.5).
private struct NoPlayableBanner: View {
    let text: String
    let importLink: () -> Void

    static func text(for group: Components.Schemas.ReleaseGroupDetail) -> String? {
        let states = group.playStates
        guard group.trackCount > 0, states.file + states.slice + states.radioExcerpt == 0 else { return nil }
        let total =
            group.trackCount == 1
            ? String(localized: "This track does not play here.")
            : String(localized: "None of these \(LibraryFormat.count(group.trackCount)) tracks play here.")
        let links = states.linkOnly
        guard links > 0 else { return total }
        let kind =
            group.linkKinds.count == 1
            ? String(localized: "a \(group.linkKinds[0].displayName) link") : String(localized: "a link")
        let have =
            links == 1
            ? String(localized: "1 has \(kind).")
            : String(localized: "\(LibraryFormat.count(links)) have \(kind).")
        return "\(total) \(have)"
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "link")
                .font(.system(size: 13))
                .foregroundStyle(Palette.text2)
                .accessibilityHidden(true)
            Text(text)
                .font(Typography.secondary)
                .foregroundStyle(Palette.text)
                .lineLimit(2)
            Spacer(minLength: 0)
            Button("Import…", action: importLink)
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .accessibilityIdentifier("tracks.banner.import")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(Palette.chip)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("tracks.banner")
    }
}

/// The design's small bordered button: height 22, padding 0 10, 1 pt border, radius 5,
/// 12 pt.
struct SmallBorderedButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Typography.secondary)
            .foregroundStyle(isEnabled ? Palette.text : Palette.text3)
            .padding(.horizontal, 10)
            .frame(height: Metrics.smallButtonHeight)
            .background(
                configuration.isPressed ? Palette.chip : Palette.bg, in: RoundedRectangle(cornerRadius: Radius.row)
            )
            .overlay(RoundedRectangle(cornerRadius: Radius.row).strokeBorder(Palette.line, lineWidth: Metrics.hairline))
            .contentShape(Rectangle())
    }
}
