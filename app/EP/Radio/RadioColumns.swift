import EPAPI
import SwiftUI

/// The Radio view's three list columns (§6): shows, the show's episodes, the episode's
/// tracklist. Keyboard as in the Library: ← and → move between the columns; moving right
/// into a column with nothing selected selects its first row; Return plays from the
/// selected entry.
struct RadioColumns: View {
    @Environment(RadioBrowser.self) private var radio
    @FocusState private var focus: RadioColumn?

    var body: some View {
        ColumnGrid(spec: .radio) {
            ListColumn(id: "shows", label: "Shows", background: Palette.side) {
                ShowsColumn(focus: $focus) { moveRight(from: .shows) }
            }
            ListColumn(id: "episodes", label: "Episodes") {
                EpisodesColumn(focus: $focus, moveLeft: { focus = .shows }) { moveRight(from: .episodes) }
            }
            ListColumn(id: "tracklist", label: "Tracklist", trailingBorder: false) {
                TracklistColumn(focus: $focus) { focus = .episodes }
            }
        }
    }

    private func moveRight(from column: RadioColumn) {
        switch column {
        case .shows:
            guard radio.location.showID != nil, let first = radio.episodes.first else { return }
            if radio.location.episodeID == nil { radio.select(episode: first.id) }
            focus = .episodes
        case .episodes:
            guard radio.location.episodeID != nil, let first = radio.entries.first else { return }
            if radio.location.entryID == nil { radio.select(entry: first.id) }
            focus = .tracklist
        case .tracklist:
            break
        }
    }
}

enum RadioColumn: Hashable {
    case shows, episodes, tracklist
}

// MARK: - Shows

/// "SHOWS · 14", then 40 pt rows: round 28 pt image, name over station. No filter field.
private struct ShowsColumn: View {
    var focus: FocusState<RadioColumn?>.Binding
    let moveRight: () -> Void

    @Environment(RadioBrowser.self) private var radio

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Shows · \(radio.shows.count)")
                .sectionLabelStyle()
                .padding(.horizontal, 14)
                .padding(.top, 10)
                .padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("shows.header")
            if radio.shows.isEmpty {
                switch radio.showsPhase {
                case .failed(let message): ColumnMessage(id: "shows", text: message) { radio.reload() }
                case .loaded: ColumnMessage(id: "shows", text: String(localized: "No radio shows yet."))
                case .idle: ConnectionStatusText()
                case .loading: DelayedSkeleton(rowHeight: RowHeight.show, cover: true)
                }
            } else {
                list
            }
        }
    }

    private var list: some View {
        ScrollViewReader { proxy in
            List(
                selection: shownSelection(radio.location.showID, focused: focus.wrappedValue == .shows) {
                    radio.select(show: $0)
                }
            ) {
                ForEach(radio.shows, id: \.id) { show in
                    ShowRow(show: show, emphasized: focus.wrappedValue == .shows && show.id == radio.location.showID)
                        .tag(show.id)
                        .softSelection(
                            focus.wrappedValue != .shows && show.id == radio.location.showID, radius: Radius.rowLarge)
                        .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
                        .listRowSeparator(.hidden)
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.show)
            .focused(focus, equals: .shows)
            .onKeyPress(.rightArrow) {
                moveRight()
                return .handled
            }
            .contextMenu(forSelectionType: String.self) { ids in
                if let show = ids.first.flatMap({ id in radio.shows.first { $0.id == id } }) {
                    Button("Copy Link") { copyNoteLink(show.link) }
                }
            }
            .onChange(of: radio.location.showID) { _, id in
                if let id { proxy.scrollTo(id) }
            }
            .accessibilityIdentifier("shows.list")
        }
    }
}

private struct ShowRow: View {
    let show: Components.Schemas.RadioShowRead
    let emphasized: Bool

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        HStack(spacing: 8) {
            CoverThumbnail(path: show.imageUrl, size: 28, radius: 14)
            VStack(alignment: .leading, spacing: 1) {
                Text(show.name).font(Typography.rowTitle).foregroundStyle(colors.primary)
                Text(show.station.displayName).font(Typography.meta).foregroundStyle(colors.tertiary)
            }
            .lineLimit(1)
            Spacer(minLength: 0)
        }
        .frame(height: RowHeight.show)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("shows.row.\(show.id)")
    }
}

// MARK: - Episodes

/// Header "Late Night Tapes" / "NTS · 46 episodes"; 48 pt rows: 36 pt cover, title over
/// "14 Mar 2025 · London".
private struct EpisodesColumn: View {
    var focus: FocusState<RadioColumn?>.Binding
    let moveLeft: () -> Void
    let moveRight: () -> Void

    @Environment(RadioBrowser.self) private var radio

    var body: some View {
        VStack(spacing: 0) {
            ColumnHeader(id: "episodes", title: radio.show?.name, meta: meta)
            content
        }
    }

    private var meta: String? {
        guard let show = radio.show else { return nil }
        let count =
            show.episodeCount == 1
            ? String(localized: "1 episode") : String(localized: "\(LibraryFormat.count(show.episodeCount)) episodes")
        return "\(show.station.displayName) · \(count)"
    }

    @ViewBuilder private var content: some View {
        if radio.location.showID == nil {
            Color.clear
        } else if radio.episodes.isEmpty {
            switch radio.episodesPhase {
            case .failed(let message): ColumnMessage(id: "episodes", text: message) { radio.reload() }
            case .loaded: ColumnMessage(id: "episodes", text: String(localized: "No episodes."))
            case .loading, .idle: DelayedSkeleton(rowHeight: RowHeight.episode, cover: true)
            }
        } else {
            list
        }
    }

    private var list: some View {
        ScrollViewReader { proxy in
            List(
                selection: shownSelection(radio.location.episodeID, focused: focus.wrappedValue == .episodes) {
                    radio.select(episode: $0)
                }
            ) {
                ForEach(radio.episodes, id: \.id) { episode in
                    EpisodeRow(
                        episode: episode,
                        emphasized: focus.wrappedValue == .episodes && episode.id == radio.location.episodeID
                    )
                    .tag(episode.id)
                    .softSelection(
                        focus.wrappedValue != .episodes && episode.id == radio.location.episodeID,
                        radius: Radius.rowLarge)
                    .listRowInsets(EdgeInsets(top: 0, leading: 6, bottom: 0, trailing: 6))
                    .listRowSeparator(.hidden)
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.episode)
            .focused(focus, equals: .episodes)
            .onKeyPress(.leftArrow) {
                moveLeft()
                return .handled
            }
            .onKeyPress(.rightArrow) {
                moveRight()
                return .handled
            }
            .contextMenu(forSelectionType: String.self) { ids in
                if let episode = ids.first.flatMap({ id in radio.episodes.first { $0.id == id } }) {
                    Button("Play Episode") {
                        radio.select(episode: episode.id)
                        Task {
                            await radio.settle()
                            radio.play(from: nil)
                        }
                    }
                    Button("Copy Link") { copyNoteLink(episode.link) }
                }
            } primaryAction: { ids in
                guard let id = ids.first else { return }
                radio.select(episode: id)
                Task {
                    await radio.settle()
                    radio.play(from: nil)
                }
            }
            .onChange(of: radio.location.episodeID) { _, id in
                if let id { proxy.scrollTo(id) }
            }
            .accessibilityIdentifier("episodes.list")
        }
    }
}

private struct EpisodeRow: View {
    let episode: Components.Schemas.RadioEpisodeRead
    let emphasized: Bool

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        HStack(spacing: 10) {
            CoverThumbnail(path: episode.imageUrl, size: 36, radius: Radius.cover)
            VStack(alignment: .leading, spacing: 2) {
                Text(episode.name).font(Typography.rowTitle).foregroundStyle(colors.primary)
                Text(RadioFormat.episodeMeta(episode)).font(Typography.meta).foregroundStyle(colors.tertiary)
            }
            .lineLimit(1)
            Spacer(minLength: 0)
        }
        .frame(height: RowHeight.episode)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("episodes.row.\(episode.id)")
    }
}

// MARK: - Tracklist

/// Header "14 Mar 2025 — Homage to the night shift" / "31 entries · 2:00:00 · 22 matched";
/// 26 pt rows on the grid `64 1fr 1.3fr 120`: time, artist, title, state. Matched entries'
/// artist and title are links into the Library. Double-click or Return plays the episode
/// from the entry; the entry playing now shows the bars.
private struct TracklistColumn: View {
    var focus: FocusState<RadioColumn?>.Binding
    let moveLeft: () -> Void

    @Environment(RadioBrowser.self) private var radio
    @Environment(Player.self) private var player
    @State private var repointing: RepointRequest?

    var body: some View {
        VStack(spacing: 0) {
            ColumnHeader(id: "tracklist", title: title, meta: radio.episode.map(RadioFormat.tracklistMeta))
            if let error = radio.entryError {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("tracklist.error")
            }
            content
        }
        .sheet(item: $repointing) { request in
            RepointSheet(entry: request.entry) { recordingID in
                radio.repoint(entry: request.id, to: recordingID)
            }
        }
    }

    private var title: String? {
        guard let episode = radio.episode, episode.id == radio.location.episodeID else { return nil }
        return [RadioFormat.day(episode.broadcastAt), episode.name].compactMap(\.self).joined(separator: " — ")
    }

    @ViewBuilder private var content: some View {
        if radio.location.episodeID == nil {
            Color.clear
        } else if let episode = radio.episode, episode.id == radio.location.episodeID {
            if episode.tracks.isEmpty {
                ColumnMessage(id: "tracklist", text: String(localized: "No tracklist."))
            } else {
                list(episode)
            }
        } else {
            switch radio.episodePhase {
            case .failed(let message): ColumnMessage(id: "tracklist", text: message) { radio.reload() }
            default: DelayedSkeleton(rowHeight: RowHeight.tracklistEntry)
            }
        }
    }

    /// The entry of this episode playing now.
    private func playingEntryID(_ episode: Components.Schemas.RadioEpisodeDetail) -> String? {
        guard case .episode(let playing) = player.current?.kind, playing.id == episode.id else { return nil }
        return player.currentEntry?.id
    }

    private func list(_ episode: Components.Schemas.RadioEpisodeDetail) -> some View {
        let playing = playingEntryID(episode)
        return ScrollViewReader { proxy in
            List(
                selection: shownSelection(radio.location.entryID, focused: focus.wrappedValue == .tracklist) {
                    radio.select(entry: $0)
                }
            ) {
                ForEach(episode.tracks, id: \.id) { entry in
                    TracklistRow(
                        entry: entry, emphasized: focus.wrappedValue == .tracklist && entry.id == radio.location.entryID,
                        isPlaying: entry.id == playing
                    )
                    .tag(entry.id)
                    .softSelection(focus.wrappedValue != .tracklist && entry.id == radio.location.entryID)
                    .listRowInsets(EdgeInsets(top: 0, leading: 0, bottom: 0, trailing: 0))
                    .listRowSeparator(.hidden)
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.tracklistEntry)
            .focused(focus, equals: .tracklist)
            .onKeyPress(.leftArrow) {
                moveLeft()
                return .handled
            }
            .contextMenu(forSelectionType: String.self) { ids in
                if let entry = ids.first.flatMap({ id in episode.tracks.first { $0.id == id } }) {
                    menu(for: entry)
                }
            } primaryAction: { ids in
                if let id = ids.first { radio.play(from: id) }
            }
            .onChange(of: radio.location.entryID) { _, id in
                if let id { proxy.scrollTo(id) }
            }
            .accessibilityIdentifier("tracklist.list")
        }
    }

    @ViewBuilder private func menu(for entry: Components.Schemas.RadioEpisodeTrackRead) -> some View {
        Button("Play from Here") { radio.play(from: entry.id) }
            .disabled(entry.startMs == nil || radio.episode?.playback == nil)
        Divider()
        Button(entry.recording == nil ? "Match to Recording…" : "Match to Another Recording…") {
            repointing = RepointRequest(entry: entry)
        }
        if entry.recording != nil {
            Button("Unmatch") { radio.repoint(entry: entry.id, to: nil) }
        }
        if let recording = entry.recording {
            Divider()
            Button("Copy Link") { copyNoteLink(recording.link) }
        }
    }
}

/// The entry whose recording is being chosen (the re-point sheet).
private struct RepointRequest: Identifiable {
    let entry: Components.Schemas.RadioEpisodeTrackRead
    var id: String { entry.id }
}

private struct TracklistRow: View {
    let entry: Components.Schemas.RadioEpisodeTrackRead
    let emphasized: Bool
    let isPlaying: Bool

    @Environment(\.openEntity) private var openEntity

    /// Artist and title share the row as `minmax(0, 1fr) minmax(0, 1.3fr)`, 8 apart.
    private static let textColumns = ColumnSpec(fractions: [1, 1.3], minimums: [0, 0], gap: 8)

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        let recording = entry.recording
        HStack(spacing: 8) {
            HStack(spacing: 4) {
                if isPlaying {
                    Image(systemName: "chart.bar.fill")
                        .font(.system(size: 9))
                        .accessibilityHidden(true)
                }
                Text(RadioFormat.time(entry))
                    .italic(entry.offsetIsEstimate)
                    .monospacedDigit()
            }
            .font(Typography.body)
            .foregroundStyle(colors.tertiary)
            .frame(width: 64, alignment: .leading)
            ColumnGrid(spec: Self.textColumns) {
                link(
                    recording?.artistCredit.name ?? entry.artistText,
                    to: recording?.artistCredit.names.first.map { EntityRef(type: .artist, id: $0.artistId) },
                    colors: colors
                )
                .frame(maxWidth: .infinity, alignment: .leading)
                link(
                    recording?.title ?? entry.titleText,
                    to: recording.map { EntityRef(type: .recording, id: $0.id) }, colors: colors
                )
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: RowHeight.tracklistEntry)
            Text(RadioFormat.state(entry))
                .font(Typography.meta)
                .foregroundStyle(
                    emphasized ? Palette.selText : entry.state == .excerptOnly ? Palette.warn : Palette.text3
                )
                .lineLimit(1)
                .frame(width: 120, alignment: .trailing)
        }
        .padding(.horizontal, 6)
        .frame(height: RowHeight.tracklistEntry)
        .contentShape(Rectangle())
        .help(RadioFormat.help(entry))
        .accessibilityElement(children: .combine)
        .accessibilityValue(isPlaying ? String(localized: "Now playing") : "")
        // The links are folded into the row: VoiceOver reaches them as actions.
        .accessibilityActions {
            if let artist = recording?.artistCredit.names.first {
                Button("Show Artist") { open(EntityRef(type: .artist, id: artist.artistId)) }
            }
            if let recording {
                Button("Show Track") { open(EntityRef(type: .recording, id: recording.id)) }
            }
        }
        .accessibilityIdentifier("tracklist.row.\(entry.id)")
    }

    private func open(_ ref: EntityRef) {
        openEntity?(OpenTarget(entity: ref), newWindow: false)
    }

    /// A matched entry's artist or title: an `--accent` link into the Library; unmatched
    /// text is plain `--text2`.
    @ViewBuilder private func link(_ text: String, to ref: EntityRef?, colors: RowForeground) -> some View {
        if let ref {
            Button {
                openEntity?(OpenTarget(entity: ref), newWindow: NSEvent.modifierFlags.contains([.command, .option]))
            } label: {
                Text(text)
                    .font(Typography.body)
                    .foregroundStyle(emphasized ? Palette.selText : Palette.accent)
                    .underline()
                    .lineLimit(1)
            }
            .buttonStyle(.plain)
            .pointerStyle(.link)
        } else {
            Text(text)
                .font(Typography.body)
                .foregroundStyle(colors.secondary)
                .lineLimit(1)
        }
    }
}
