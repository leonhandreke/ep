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
    /// What this window was opened to show (⌥⌘-click), shown once connected.
    @State private var pendingTarget: OpenTarget?
    /// A radio show, episode or entry to show; the Radio view (stage 7) picks it up.
    @State private var radioTarget: OpenTarget?
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
        _pendingTarget = State(initialValue: initialTarget)
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
                            album: albumDetails, track: trackDetails)
                            .frame(height: Metrics.panelHeaderHeight)
                    } else {
                        DetailDivider(height: panels, available: available) { detailPanelsHeight = $0 }
                        DetailPanels(
                            section: section, collapsed: $detailPanelsCollapsed, artist: artistDetails,
                            album: albumDetails, track: trackDetails)
                            .frame(height: panels)
                    }
                }
            }
            PlayerBar(showPlaying: showPlaying)
        }
        .background(Palette.bg)
        .frame(minWidth: WindowMetrics.minimumWidth, minHeight: WindowMetrics.minimumHeight)
        .navigationTitle("EP")
        .environment(artists)
        .environment(browser)
        .environment(\.openEntity, OpenEntityAction(handler: open))
        .toolbar { MainToolbar(section: $section, browser: browser) }
        .toolbarBackground(Palette.bar, for: .windowToolbar)
        .focusedSceneValue(\.detailPanelsCollapsed, $detailPanelsCollapsed)
        .focusedSceneValue(\.libraryBrowser, browser)
        .focusedSceneValue(\.showPlaying, showPlaying)
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
        .onChange(of: appearsActive) { _, active in
            guard active else { return }
            if connection.isConnected { reloadLibrary() }
            Task { await connection.refresh() }
        }
    }

    /// "Show playing" (⌘L, the player's title): the Library, at what plays.
    private func showPlaying() {
        guard let target = player.libraryTarget else { return }
        section = .library
        browser.navigate(to: target)
    }

    private func reloadLibrary() {
        NoteDocument.flushUnsaved()
        artists.reload()
        browser.reload()
        artistDetails.reload()
        albumDetails.reload()
        trackDetails.reload()
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
            radioTarget = target
            section = .radio
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
