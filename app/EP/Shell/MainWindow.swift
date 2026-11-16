import EPAPI
import SwiftUI

/// The two views of the main window, switched in the toolbar.
enum AppSection: String, CaseIterable, Identifiable {
    case library, radio

    var id: Self { self }

    var title: LocalizedStringKey {
        switch self {
        case .library: "Library"
        case .radio: "Radio"
        }
    }

    var columns: ColumnSpec {
        switch self {
        case .library: .library
        case .radio: .radio
        }
    }
}

/// What a new main window opens to (⌥⌘-click). The token makes every request a new
/// window, even for a target another window was opened to.
nonisolated struct WindowTarget: Hashable, Codable, Sendable {
    var target: OpenTarget
    var token = UUID()
}

/// The main window (design §3): toolbar, the unreachable banner, three list columns, the
/// divider, the detail panels and the player bar. Each window keeps its own section,
/// panel height and collapsed state.
struct MainWindow: View {
    @Environment(ServerConnection.self) private var connection
    @Environment(Player.self) private var player
    @Environment(\.appearsActive) private var appearsActive
    /// This window's Library: its artist list (filter, toggle) and its selection and history.
    @State private var artists: ArtistList
    @State private var browser: LibraryBrowser
    /// The three detail panels (artist, album, track), following the selection.
    @State private var artistDetails: EntityDetails
    @State private var albumDetails: EntityDetails
    @State private var trackDetails: EntityDetails
    /// This window's Radio view: shows, episodes, tracklist, history; its tabbed panel.
    @State private var radio: RadioBrowser
    @State private var radioDetails: EntityDetails
    /// The search palette (⌘K, ⌘F) while open; it keeps what was typed while closed.
    @State private var search: SearchStore
    @State private var searchOpen = false
    /// The edit, create or MusicBrainz sheet over this window.
    @State private var sheet: WindowSheet?
    /// What this window was opened to show (⌥⌘-click), shown once connected.
    @State private var pendingTarget: OpenTarget?
    @Environment(\.openWindow) private var openWindow
    @SceneStorage("section") private var section = AppSection.library
    @SceneStorage("detailPanelsCollapsed") private var detailPanelsCollapsed = false
    /// Detail panel height in points; 0 until first laid out (then half the space).
    @SceneStorage("detailPanelsHeight") private var detailPanelsHeight: Double = 0

    init(connection: ServerConnection, player: Player, initialTarget: OpenTarget? = nil) {
        let browser = LibraryBrowser(connection: connection)
        browser.onPlay = { [weak player] in player?.play($0) }
        _artists = State(initialValue: ArtistList(connection: connection))
        _browser = State(initialValue: browser)
        let artist = EntityDetails(connection: connection)
        let album = EntityDetails(connection: connection)
        let track = EntityDetails(connection: connection)
        // A saved note may link to what another panel shows: its "Linked from" reloads.
        artist.onNoteSaved = { [weak album, weak track] in album?.reload(); track?.reload() }
        album.onNoteSaved = { [weak artist, weak track] in artist?.reload(); track?.reload() }
        track.onNoteSaved = { [weak artist, weak album] in artist?.reload(); album?.reload() }
        _artistDetails = State(initialValue: artist)
        _albumDetails = State(initialValue: album)
        _trackDetails = State(initialValue: track)
        let radio = RadioBrowser(connection: connection)
        radio.onPlay = { [weak player] episode, entry in player?.playEpisode(episode, from: entry) }
        _radio = State(initialValue: radio)
        _radioDetails = State(initialValue: EntityDetails(connection: connection))
        _search = State(initialValue: SearchStore(connection: connection))
        _pendingTarget = State(initialValue: initialTarget)
    }

    /// The history Back / Forward act on: the shown view's.
    private var history: any HistoryNavigating {
        section == .radio ? radio : browser
    }

    var body: some View {
        VStack(spacing: 0) {
            if case .unreachable = connection.state {
                UnreachableBanner()
            }
            GeometryReader { geometry in
                let available = geometry.size.height - Metrics.dividerHeight
                let panels = clampedPanelsHeight(available: available)
                VStack(spacing: 0) {
                    ListColumns(section: section)
                        .frame(maxHeight: .infinity)
                    if detailPanelsCollapsed {
                        DetailPanels(
                            section: section, collapsed: $detailPanelsCollapsed, artist: artistDetails,
                            album: albumDetails, track: trackDetails, radio: radioDetails)
                            .frame(height: Metrics.panelHeaderHeight)
                    } else {
                        DetailDivider(height: panels, available: available) { detailPanelsHeight = $0 }
                        DetailPanels(
                            section: section, collapsed: $detailPanelsCollapsed, artist: artistDetails,
                            album: albumDetails, track: trackDetails, radio: radioDetails)
                            .frame(height: panels)
                    }
                }
            }
            PlayerBar(showPlaying: showPlaying)
        }
        .overlay {
            if searchOpen {
                SearchPalette(store: search) { searchOpen = false }
            }
        }
        .background(Palette.bg)
        .frame(minWidth: WindowMetrics.minimumWidth, minHeight: WindowMetrics.minimumHeight)
        .navigationTitle("EP")
        .environment(artists)
        .environment(browser)
        .environment(radio)
        .environment(\.openEntity, OpenEntityAction(handler: open))
        .environment(\.showTag) { tag in openSearch(tag: tag) }
        .environment(\.presentSheet) { sheet = $0 }
        .sheet(item: $sheet) { sheet in
            sheetView(sheet)
        }
        .toolbar { MainToolbar(section: $section, history: history) { openSearch(tag: nil) } }
        .toolbarBackground(Palette.bar, for: .windowToolbar)
        .focusedSceneValue(\.detailPanelsCollapsed, $detailPanelsCollapsed)
        .focusedSceneValue(\.history, history)
        .focusedSceneValue(\.showPlaying, showPlaying)
        .focusedSceneValue(\.openSearch) { openSearch(tag: nil) }
        .focusedSceneValue(\.newEntity) { kind in newEntity(kind) }
        // The server is the only source of truth and does not push: load once connected
        // (again after being unreachable), and reload when the window becomes active.
        .onChange(of: connection.isConnected, initial: true) { _, connected in
            guard connected else { return }
            reloadLibrary()
            if let target = pendingTarget {
                pendingTarget = nil
                open(target, newWindow: false)
            }
        }
        // The panels follow the selection; a track's note and tags are its recording's.
        .onChange(of: browser.location.artistID, initial: true) { _, id in
            artistDetails.show(id.map { EntityRef(type: .artist, id: $0) })
        }
        .onChange(of: browser.location.album, initial: true) { _, album in
            albumDetails.show(album?.releaseGroupID.map { EntityRef(type: .releaseGroup, id: $0) })
        }
        .onChange(of: browser.selectedTrack?.recordingID, initial: true) { _, id in
            trackDetails.show(id.map { EntityRef(type: .recording, id: $0) })
        }
        // The Radio panel: the episode, else the show.
        .onChange(of: radio.location, initial: true) { _, location in
            radioDetails.show(
                location.episodeID.map { EntityRef(type: .radioEpisode, id: $0) }
                    ?? location.showID.map { EntityRef(type: .radioShow, id: $0) })
        }
        .onChange(of: appearsActive) { _, active in
            guard active else { return }
            if connection.isConnected { reloadLibrary() }
            Task { await connection.refresh() }
        }
    }

    @ViewBuilder private func sheetView(_ sheet: WindowSheet) -> some View {
        let done = sheet.done ?? { created in show(created) }
        switch sheet.kind {
        case .edit(let target):
            EditSheet(target: target) { reloadLibrary() }
        case .create(let kinds, let name, let credit):
            CreateSheet(kinds: kinds, name: name, credit: credit) { created in
                reloadLibrary()
                done(created)
            }
        case .musicBrainz(let query):
            MusicBrainzSheet(query: query) { created in
                reloadLibrary()
                done(created)
            }
        }
    }

    /// Something just created: shown in the Library.
    private func show(_ created: CreatedEntity) {
        open(OpenTarget(entity: created.ref), newWindow: false)
    }

    /// File ▸ New Artist… / Album… / Recording…: an album or recording is credited to the
    /// artist shown, to begin with.
    private func newEntity(_ kind: Creator.Kind) {
        let credit =
            kind == .artist || section != .library
            ? [] : browser.artist.map { [CreditPart(artistID: $0.id, name: $0.name, joinPhrase: "")] } ?? []
        sheet = WindowSheet(kind: .create(kinds: [kind], name: "", credit: credit))
    }

    /// Opens the search palette; a tag click opens it filtered to the tag, with nothing
    /// typed (decision 7).
    private func openSearch(tag: Components.Schemas.TagRead?) {
        if let tag {
            search.update("")
            search.filter(by: tag)
        } else if !searchOpen {
            search.filter(by: nil)
        }
        searchOpen = true
    }

    /// "Show playing" (⌘L, the player's title): an episode played from the Radio view in
    /// the Radio view at the entry playing; anything else in the Library.
    private func showPlaying() {
        if let target = player.radioTarget {
            section = .radio
            radio.navigate(to: target)
        } else if let target = player.libraryTarget {
            section = .library
            browser.navigate(to: target)
        }
    }

    private func reloadLibrary() {
        NoteDocument.flushUnsaved()
        artists.reload()
        browser.reload()
        artistDetails.reload()
        albumDetails.reload()
        trackDetails.reload()
        radio.reload()
        radioDetails.reload()
    }

    /// Opens a link, backlink or radio appearance target (`OpenEntityAction`): here, or in
    /// a new main window with its own history (⌥⌘-click, decision 13).
    private func open(_ target: OpenTarget, newWindow: Bool) {
        if newWindow {
            openWindow(id: "main", value: WindowTarget(target: target))
        } else if let entity = target.entity.libraryEntity {
            section = .library
            browser.navigate(to: entity)
        } else {
            section = .radio
            radio.navigate(to: target)
        }
    }

    private func clampedPanelsHeight(available: CGFloat) -> CGFloat {
        let wanted = detailPanelsHeight > 0 ? detailPanelsHeight : available / 2
        let maximum = max(WindowMetrics.minimumPanelsHeight, available - WindowMetrics.minimumListsHeight)
        return min(max(wanted, WindowMetrics.minimumPanelsHeight), maximum)
    }
}

#Preview {
    let connection = ServerConnection(serverURL: AppSettings.defaultServerURL)
    let player = Player(connection: connection, engine: AudioOutput())
    MainWindow(connection: connection, player: player)
        .environment(connection)
        .environment(player)
        .environment(CoverImages())
        .frame(width: 1440, height: 900)
}
