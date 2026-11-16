import SwiftUI

@main
struct EPApp: App {
    @State private var settings: AppSettings
    @State private var connection: ServerConnection
    @State private var covers = CoverImages()
    /// The one player all windows share (decision 13).
    @State private var player: Player

    init() {
        let settings = AppSettings()
        let connection = ServerConnection(serverURL: settings.serverURL)
        let player = Player(connection: connection, engine: AudioOutput())
        _settings = State(initialValue: settings)
        _connection = State(initialValue: connection)
        _player = State(initialValue: player)
        LaunchOptions.applyAppearance()
        // As the unit tests' host the app stays offline: tests use their own stubs.
        if !Self.isUnitTestHost {
            player.nowPlaying = NowPlayingCenter(player: player)
            PlaybackKeys.install(player)
            Task { await connection.connect() }
        }
    }

    /// True when the app was launched to host EPTests (not for UI tests, which launch it
    /// as a separate process).
    static let isUnitTestHost = ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil

    var body: some Scene {
        WindowGroup("EP", id: "main") {
            MainWindow(connection: connection, player: player)
                .environment(settings)
                .environment(connection)
                .environment(covers)
                .environment(player)
        }
        .defaultSize(LaunchOptions.windowSize)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands { AppCommands(player: player) }

        Settings {
            SettingsView()
                .environment(settings)
                .environment(connection)
        }
    }
}

/// Menu bar additions. Commands act on the focused main window through focused values.
struct AppCommands: Commands {
    let player: Player
    @FocusedValue(\.detailPanelsCollapsed) private var detailPanelsCollapsed
    @FocusedValue(\.libraryBrowser) private var browser
    @FocusedValue(\.showPlaying) private var showPlaying

    var body: some Commands {
        CommandMenu("Go") {
            Button("Back") { browser?.goBack() }
                .keyboardShortcut("[", modifiers: .command)
                .disabled(browser?.canGoBack != true)
            Button("Forward") { browser?.goForward() }
                .keyboardShortcut("]", modifiers: .command)
                .disabled(browser?.canGoForward != true)
        }
        CommandGroup(after: .toolbar) {
            Button(detailPanelsCollapsed?.wrappedValue == true ? "Show Detail Panels" : "Hide Detail Panels") {
                detailPanelsCollapsed?.wrappedValue.toggle()
            }
            .keyboardShortcut("d", modifiers: [.command, .option])
            .disabled(detailPanelsCollapsed == nil)
            Divider()
        }
        // Space plays and pauses outside text fields (`PlaybackKeys`); a menu key
        // equivalent would also take it from text fields, so these items have none.
        CommandMenu("Controls") {
            Button(player.isPlaying ? "Pause" : "Play") { player.togglePlayPause() }
                .disabled(player.current == nil)
            Button("Next") { player.next() }
                .disabled(player.current == nil)
            Button("Previous") { player.previous() }
                .disabled(player.current == nil)
            Divider()
            Button("Show Playing") { showPlaying?() }
                .keyboardShortcut("l", modifiers: .command)
                .disabled(showPlaying == nil || player.libraryTarget == nil)
        }
    }
}

extension FocusedValues {
    /// Whether the focused main window's detail panels are collapsed (⌥⌘D).
    @Entry var detailPanelsCollapsed: Binding<Bool>?
    /// The focused main window's Library (Back / Forward).
    @Entry var libraryBrowser: LibraryBrowser?
    /// Shows what plays in the focused main window's Library (⌘L).
    @Entry var showPlaying: (() -> Void)?
}
