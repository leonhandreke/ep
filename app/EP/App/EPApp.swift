import AppKit
import SwiftUI

/// Quitting waits for the notes being saved; edits that cannot be saved (changed elsewhere,
/// server unreachable) are never dropped without asking.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !NoteDocument.unsaved.isEmpty else { return .terminateNow }
        Task { @MainActor in
            let saved = await NoteDocument.settleUnsaved(timeout: .seconds(5))
            sender.reply(toApplicationShouldTerminate: saved || Self.confirmDiscard(NoteDocument.unsaved.count))
        }
        return .terminateLater
    }

    private static func confirmDiscard(_ count: Int) -> Bool {
        let alert = NSAlert()
        alert.messageText = String(localized: "Some notes are not saved")
        alert.informativeText = String(
            localized: "\(count) note(s) could not be saved: they were changed elsewhere or the server cannot be reached. Quitting now loses these edits.")
        alert.addButton(withTitle: String(localized: "Cancel"))
        alert.addButton(withTitle: String(localized: "Quit Anyway"))
        return alert.runModal() == .alertSecondButtonReturn
    }
}

@main
struct EPApp: App {
    @NSApplicationDelegateAdaptor private var appDelegate: AppDelegate
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
        WindowGroup("EP", id: "main", for: WindowTarget.self) { $target in
            MainWindow(connection: connection, player: player, initialTarget: target?.target)
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
    @FocusedValue(\.history) private var history
    @FocusedValue(\.showPlaying) private var showPlaying
    @FocusedValue(\.openSearch) private var openSearch
    @FocusedValue(\.editSelection) private var editSelection
    @FocusedValue(\.newEntity) private var newEntity

    var body: some Commands {
        CommandMenu("Go") {
            Button("Back") { history?.goBack() }
                .keyboardShortcut("[", modifiers: .command)
                .disabled(history?.canGoBack != true)
            Button("Forward") { history?.goForward() }
                .keyboardShortcut("]", modifiers: .command)
                .disabled(history?.canGoForward != true)
        }
        CommandGroup(after: .newItem) {
            Divider()
            Button("New Artist…") { newEntity?(.artist) }
                .disabled(newEntity == nil)
            Button("New Album…") { newEntity?(.album) }
                .disabled(newEntity == nil)
            Button("New Recording…") { newEntity?(.recording) }
                .disabled(newEntity == nil)
        }
        // Decision 6: ⌘E edits the selection of the focused list.
        CommandGroup(after: .pasteboard) {
            Divider()
            Button("Edit…") { editSelection?() }
                .keyboardShortcut("e", modifiers: .command)
                .disabled(editSelection == nil)
        }
        // Decision 9: ⌘K and ⌘F both open the search palette (in place of the text
        // Find commands, which no view here uses).
        CommandGroup(replacing: .textEditing) {
            Button("Search…") { openSearch?() }
                .keyboardShortcut("k", modifiers: .command)
                .disabled(openSearch == nil)
            Button("Find…") { openSearch?() }
                .keyboardShortcut("f", modifiers: .command)
                .disabled(openSearch == nil)
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
                .disabled(showPlaying == nil || !player.canShowPlaying)
        }
    }
}

extension FocusedValues {
    /// Whether the focused main window's detail panels are collapsed (⌥⌘D).
    @Entry var detailPanelsCollapsed: Binding<Bool>?
    /// The focused main window's shown history: Library or Radio (Back / Forward).
    @Entry var history: (any HistoryNavigating)?
    /// Shows what plays in the focused main window (⌘L).
    @Entry var showPlaying: (() -> Void)?
    /// Opens the focused main window's search palette (⌘K, ⌘F).
    @Entry var openSearch: (() -> Void)?
    /// Edits what is selected in the focused list (⌘E); set by the list while it has focus.
    @Entry var editSelection: (() -> Void)?
    /// Creates an artist, album or recording by hand in the focused main window.
    @Entry var newEntity: ((Creator.Kind) -> Void)?
}
