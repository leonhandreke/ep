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
    @SceneStorage("section") private var section = AppSection.library
    @SceneStorage("detailPanelsCollapsed") private var detailPanelsCollapsed = false
    /// Detail panel height in points; 0 until first laid out (then half the space).
    @SceneStorage("detailPanelsHeight") private var detailPanelsHeight: Double = 0

    init(connection: ServerConnection, player: Player) {
        let browser = LibraryBrowser(connection: connection)
        browser.onPlay = { [weak player] in player?.play($0) }
        _artists = State(initialValue: ArtistList(connection: connection))
        _browser = State(initialValue: browser)
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
                        DetailPanels(section: section, collapsed: $detailPanelsCollapsed)
                            .frame(height: Metrics.panelHeaderHeight)
                    } else {
                        DetailDivider(height: panels, available: available) { detailPanelsHeight = $0 }
                        DetailPanels(section: section, collapsed: $detailPanelsCollapsed)
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
        .toolbar { MainToolbar(section: $section, browser: browser) }
        .toolbarBackground(Palette.bar, for: .windowToolbar)
        .focusedSceneValue(\.detailPanelsCollapsed, $detailPanelsCollapsed)
        .focusedSceneValue(\.libraryBrowser, browser)
        .focusedSceneValue(\.showPlaying, showPlaying)
        // The server is the only source of truth and does not push: load once connected
        // (again after being unreachable), and reload when the window becomes active.
        .onChange(of: connection.isConnected, initial: true) { _, connected in
            if connected { reloadLibrary() }
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
        artists.reload()
        browser.reload()
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
