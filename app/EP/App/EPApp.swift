import SwiftUI

@main
struct EPApp: App {
    @State private var settings: AppSettings
    @State private var connection: ServerConnection

    init() {
        let settings = AppSettings()
        let connection = ServerConnection(serverURL: settings.serverURL)
        _settings = State(initialValue: settings)
        _connection = State(initialValue: connection)
        LaunchOptions.applyAppearance()
        // As the unit tests' host the app stays offline: tests use their own stubs.
        if !Self.isUnitTestHost {
            Task { await connection.connect() }
        }
    }

    /// True when the app was launched to host EPTests (not for UI tests, which launch it
    /// as a separate process).
    static let isUnitTestHost = ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil

    var body: some Scene {
        WindowGroup("EP", id: "main") {
            MainWindow()
                .environment(settings)
                .environment(connection)
        }
        .defaultSize(LaunchOptions.windowSize)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands { AppCommands() }

        Settings {
            SettingsView()
                .environment(settings)
                .environment(connection)
        }
    }
}

/// Menu bar additions. Commands act on the focused main window through focused values.
struct AppCommands: Commands {
    @FocusedValue(\.detailPanelsCollapsed) private var detailPanelsCollapsed

    var body: some Commands {
        CommandGroup(after: .toolbar) {
            Button(detailPanelsCollapsed?.wrappedValue == true ? "Show Detail Panels" : "Hide Detail Panels") {
                detailPanelsCollapsed?.wrappedValue.toggle()
            }
            .keyboardShortcut("d", modifiers: [.command, .option])
            .disabled(detailPanelsCollapsed == nil)
            Divider()
        }
    }
}

extension FocusedValues {
    /// Whether the focused main window's detail panels are collapsed (⌥⌘D).
    @Entry var detailPanelsCollapsed: Binding<Bool>?
}
