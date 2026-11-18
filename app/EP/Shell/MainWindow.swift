import EPAPI
import SwiftUI

/// The two views of the main window, switched in the toolbar.
nonisolated enum AppSection: String, CaseIterable, Identifiable {
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
    @Environment(ImportStore.self) private var imports
    @Environment(\.appearsActive) private var appearsActive
    /// This window's one navigation history, across the Library and the Radio view: the
    /// view shown and where each is.
    @State private var navigation: WindowNavigation
    /// This window's Library: its artist list (filter, toggle) and its selection.
    @State private var artists: ArtistList
    @State private var browser: LibraryBrowser
    /// The three detail panels (artist, album, track), following the selection.
    @State private var artistDetails: EntityDetails
    @State private var albumDetails: EntityDetails
    @State private var trackDetails: EntityDetails
    /// This window's Radio view: shows, episodes, tracklist; its tabbed panel.
    @State private var radio: RadioBrowser
    @State private var radioDetails: EntityDetails
    /// The search palette (⌘K, ⌘F) while open; it keeps what was typed while closed.
    @State private var search: SearchStore
    @State private var searchOpen = false
    /// The edit, create or MusicBrainz sheet over this window.
    @State private var sheet: WindowSheet?
    /// What this window was opened to show (⌥⌘-click), shown once connected.
    @State private var pendingTarget: OpenTarget?
    /// Tells this window apart for the import toast, which shows in the active window only.
    @State private var windowID = UUID()
    @Environment(\.openWindow) private var openWindow
    /// The view shown, kept for window restoration (the history holds it while open).
    @SceneStorage("section") private var storedSection = AppSection.library
    @SceneStorage("detailPanelsCollapsed") private var detailPanelsCollapsed = false
    /// Detail panel height in points; 0 until first laid out (then half the space).
    @SceneStorage("detailPanelsHeight") private var detailPanelsHeight: Double = 0

    init(connection: ServerConnection, player: Player, initialTarget: OpenTarget? = nil) {
        let navigation = WindowNavigation()
        _navigation = State(initialValue: navigation)
        let browser = LibraryBrowser(connection: connection, navigation: navigation)
        browser.onPlay = { [weak player] in player?.play($0) }
        _artists = State(initialValue: ArtistList(connection: connection))
        _browser = State(initialValue: browser)
        let artist = EntityDetails(connection: connection)
        let album = EntityDetails(connection: connection)
        let track = EntityDetails(connection: connection)
        // A saved note may link to what another panel shows: its "Linked from" reloads.
        artist.onNoteSaved = { [weak album, weak track] in
            album?.reload(); track?.reload()
        }
        album.onNoteSaved = { [weak artist, weak track] in
            artist?.reload(); track?.reload()
        }
        track.onNoteSaved = { [weak artist, weak album] in
            artist?.reload(); album?.reload()
        }
        _artistDetails = State(initialValue: artist)
        _albumDetails = State(initialValue: album)
        _trackDetails = State(initialValue: track)
        let radio = RadioBrowser(connection: connection, navigation: navigation)
        radio.onPlay = { [weak player] episode, entry in player?.playEpisode(episode, from: entry) }
        _radio = State(initialValue: radio)
        _radioDetails = State(initialValue: EntityDetails(connection: connection))
        _search = State(initialValue: SearchStore(connection: connection))
        _pendingTarget = State(initialValue: initialTarget)
    }

    /// The view shown: part of the window's location.
    private var section: AppSection { navigation.location.section }

    /// The toolbar's and the View menu's view switch: a navigation of its own.
    private var sectionBinding: Binding<AppSection> {
        Binding(get: { navigation.location.section }, set: { navigation.show(section: $0) })
    }

    var body: some View {
        applyModifiers(content)
    }

    private var content: some View {
        frame
            .overlay(alignment: .bottomTrailing) { toast }
            .animation(.easeOut(duration: 0.2), value: imports.toast)
            .overlay {
                if searchOpen {
                    SearchPalette(store: search) { searchOpen = false }
                }
            }
            .background { PasteLinkMonitor { link in openImport(link: link) } }
            .background(Palette.bg)
            .frame(minWidth: WindowMetrics.minimumWidth, minHeight: WindowMetrics.minimumHeight)
            .navigationTitle("EP")
            // The restored window's view; afterwards the history's view is the one kept.
            .onAppear { navigation.restore(section: storedSection) }
            .onChange(of: navigation.location.section) { _, section in storedSection = section }
    }

    @ViewBuilder private var toast: some View {
        if let toast = imports.toast, imports.showsToast(in: windowID) {
            ImportToast(toast: toast) { open(toast) }
                .padding(.trailing, 16)
                .padding(.bottom, Metrics.playerBarHeight + 12)
                .transition(.move(edge: .trailing).combined(with: .opacity))
        }
    }

    /// The banner, the columns, the divider, the detail panels and the player bar.
    private var frame: some View {
        VStack(spacing: 0) {
            if connection.isOffline {
                UnreachableBanner()
            }
            if showsEmptyLibrary {
                EmptyLibraryView(importer: { openImport(link: nil) })
            } else {
                listsAndPanels
                    // §11.2: what was last loaded stays visible, at 55 % opacity.
                    .opacity(connection.isOffline ? 0.55 : 1)
            }
            PlayerBar(showPlaying: showPlaying)
        }
    }

    /// §11.1: a library with nothing in it (first launch) shows how to bring music in,
    /// in place of the Library's columns and panels.
    private var showsEmptyLibrary: Bool {
        guard section == .library, case .connected(let server) = connection.state else { return false }
        return server.counts.isEmptyLibrary
    }

    private var listsAndPanels: some View {
        GeometryReader { geometry in
                let available = geometry.size.height - Metrics.dividerHeight
                let panels = clampedPanelsHeight(available: available)
                VStack(spacing: 0) {
                    ListColumns(section: section)
                        .frame(maxHeight: .infinity)
                    if detailPanelsCollapsed {
                        DetailPanels(
                            section: section, collapsed: $detailPanelsCollapsed, artist: artistDetails,
                            album: albumDetails, track: trackDetails, radio: radioDetails
                        )
                        .frame(height: Metrics.panelHeaderHeight)
                    } else {
                        DetailDivider(height: panels, available: available) { detailPanelsHeight = $0 }
                        DetailPanels(
                            section: section, collapsed: $detailPanelsCollapsed, artist: artistDetails,
                            album: albumDetails, track: trackDetails, radio: radioDetails
                        )
                        .frame(height: panels)
                    }
                }
            }
    }

    /// Environment, sheets, toolbar, focused values and reloads.
    private func applyModifiers(_ content: some View) -> some View {
        content
            .environment(artists)
            .environment(browser)
            .environment(radio)
            .environment(\.openEntity, OpenEntityAction(handler: open))
            .environment(\.showTag) { tag in openSearch(tag: tag) }
            .environment(\.presentSheet) { sheet = $0 }
            .environment(\.openImport) { link in openImport(link: link) }
            .sheet(item: $sheet) { sheet in
                sheetView(sheet)
            }
            .toolbar {
                MainToolbar(
                    section: sectionBinding, history: navigation, search: { openSearch(tag: nil) },
                    importer: { openImport(link: nil) })
            }
            .toolbarBackground(Palette.bar, for: .windowToolbar)
            .focusedSceneValue(\.detailPanelsCollapsed, $detailPanelsCollapsed)
            .focusedSceneValue(\.history, navigation)
            .focusedSceneValue(\.showPlaying, showPlaying)
            .focusedSceneValue(\.openSearch) { openSearch(tag: nil) }
            .focusedSceneValue(\.newEntity) { kind in newEntity(kind) }
            .focusedSceneValue(\.section, sectionBinding)
            .focusedSceneValue(\.importClipboardLink) {
                if let link = PasteLinkMonitor.MonitorView.link(on: .general) { openImport(link: link) }
            }
            .focusedSceneValue(\.openImports) { sheet = WindowSheet(kind: .importer(link: nil, focusLink: false)) }
            // The server is the only source of truth and does not push: load once connected
            // (again after being unreachable), and reload when the window becomes active.
            .onChange(of: connection.isConnected, initial: true) { _, connected in
                guard connected else { return }
                reloadLibrary()
                imports.refresh()
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
            .onChange(of: appearsActive, initial: true) { _, active in
                if active { imports.toastWindow = windowID }
            }
            .onChange(of: appearsActive) { _, active in
                guard active else { return }
                if connection.isConnected {
                    reloadLibrary()
                    imports.refresh()
                }
                Task { await connection.refresh() }
            }
            .onDisappear {
                if imports.toastWindow == windowID { imports.toastWindow = nil }
            }
            // A job put something into the library: what this window shows may have changed.
            // (The counts too: the empty library gives way to the columns.)
            .onChange(of: imports.libraryChanges) {
                reloadLibrary()
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
        case .importer(let link, let focusLink):
            ImportSheet(
                link: link, focusLink: focusLink,
                review: { id in self.sheet = WindowSheet(kind: .review(jobID: id)) },
                showExisting: { ref in
                    self.sheet = nil
                    open(OpenTarget(entity: ref), newWindow: false)
                })
        case .review(let jobID):
            ReviewSheet(jobID: jobID) { job in imports.started(job) }
        }
    }

    /// The import sheet, with a link to start from (⌘V, a link-only track's Import…).
    private func openImport(link: String?) {
        sheet = WindowSheet(kind: .importer(link: link))
    }

    /// The toast's button: a job to review opens its review sheet; any other shows the
    /// import sheet.
    private func open(_ toast: ImportStore.Toast) {
        imports.dismissToast()
        if toast.kind == .review {
            sheet = WindowSheet(kind: .review(jobID: toast.jobID))
        } else {
            sheet = WindowSheet(kind: .importer(link: nil, focusLink: false))
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
    /// the Radio view at the entry playing; anything else in the Library. The view switches
    /// with the navigation, as one history entry.
    private func showPlaying() {
        if let target = player.radioTarget {
            radio.navigate(to: target)
        } else if let target = player.libraryTarget {
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

    /// Opens a link, backlink or radio appearance target (`OpenEntityAction`): here (the
    /// view switches with the navigation, one entry of the window's history), or in a new
    /// main window with its own history (⌥⌘-click, decision 13).
    private func open(_ target: OpenTarget, newWindow: Bool) {
        if newWindow {
            openWindow(id: "main", value: WindowTarget(target: target))
        } else if let entity = target.entity.libraryEntity {
            browser.navigate(to: entity)
        } else {
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
        .environment(ImportStore(connection: connection))
        .environment(CoverImages())
        .frame(width: 1440, height: 900)
}
