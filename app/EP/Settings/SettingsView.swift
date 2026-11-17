import AppKit
import EPAPI
import SwiftUI

/// Settings (§10): one pane. Server address with its connection status, the server's
/// version and library counts (`GET /server`), the library location (read-only, set on
/// the server), and the watched folders with an enable checkbox each and + / −.
struct SettingsView: View {
    @Environment(AppSettings.self) private var settings
    @Environment(ServerConnection.self) private var connection
    @Environment(WatchFolders.self) private var folders
    @State private var address = ""
    @State private var isInvalid = false
    @State private var selection: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 14) {
                GridRow {
                    label("Server")
                    server
                }
                GridRow {
                    label("Library location")
                    libraryLocation
                }
            }
            watchedFolders
        }
        .font(Typography.body)
        .padding(24)
        .frame(width: 720, alignment: .topLeading)
        .frame(minHeight: 480, alignment: .top)
        .onAppear { address = settings.serverURL.absoluteString }
        // The folders are the server's: load them whenever the server (again) answers.
        .task(id: connection.isConnected) {
            if connection.isConnected { await folders.load() }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("settings.window")
    }

    private func label(_ text: LocalizedStringKey) -> some View {
        Text(text)
            .foregroundStyle(Palette.text2)
            .frame(width: 150, alignment: .trailing)
            .gridColumnAlignment(.trailing)
    }

    // MARK: - Server

    private var server: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                TextField("Server", text: $address, prompt: Text(AppSettings.defaultServerURL.absoluteString))
                    .font(Typography.mono)
                    .labelsHidden()
                    .frame(width: 280)
                    .disabled(settings.isServerURLOverridden)
                    .onSubmit(apply)
                    .accessibilityLabel("Server address")
                    .accessibilityIdentifier("settings.serverURL")
                ConnectionBadge()
                Button(connection.isConnected ? "Test" : "Retry", action: test)
                    .controlSize(.small)
                    .disabled(connection.state.isConnecting)
                    .help("Connect to this address now")
                    .accessibilityLabel("Test connection")
                    .accessibilityIdentifier("settings.retry")
            }
            if isInvalid {
                Text("Enter an address like http://localhost:8000.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .accessibilityIdentifier("settings.invalid")
            } else if settings.isServerURLOverridden {
                Text("Set for this run by EP_SERVER_URL or -serverURL.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
            }
            switch connection.state {
            case .connected(let server):
                Text(
                    "Server \(server.version) · \(LibraryFormat.count(server.counts.artists)) artists · \(LibraryFormat.count(server.counts.tracks)) tracks"
                )
                .font(Typography.secondary)
                .foregroundStyle(Palette.text2)
                .accessibilityIdentifier("settings.serverInfo")
            case .unreachable(let message):
                Text(message)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    .lineLimit(2)
                    .accessibilityIdentifier("settings.serverError")
            case .connecting:
                EmptyView()
            }
        }
    }

    /// Return in the field, or Test / Retry: a changed address is saved and used; the same
    /// address is tried again.
    private func test() {
        if settings.isServerURLOverridden || AppSettings.parseServerURL(address) == settings.serverURL {
            Task { await connection.connect() }
        } else {
            apply()
        }
    }

    private func apply() {
        guard let url = AppSettings.parseServerURL(address) else {
            isInvalid = true
            return
        }
        isInvalid = false
        address = url.absoluteString
        settings.serverURL = url
        Task { await connection.use(serverURL: url) }
    }

    // MARK: - Library location

    @ViewBuilder private var libraryLocation: some View {
        if case .connected(let server) = connection.state {
            HStack(spacing: 6) {
                Text(ImportFormat.abbreviatingHome(server.libraryDir.hostPath ?? server.libraryDir.path))
                    .font(Typography.mono)
                    .textSelection(.enabled)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text("(set on the server)")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("settings.libraryLocation")
        } else {
            Text("Known once the server answers.")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
        }
    }

    // MARK: - Watched folders

    private var watchedFolders: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("Watched folders").font(Typography.name)
                Text("New folders here are imported automatically.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
            }
            VStack(spacing: 0) {
                folderList
                HairlineDivider()
                folderButtons
            }
            .overlay(RoundedRectangle(cornerRadius: Radius.largeButton).strokeBorder(Palette.line))
            .clipShape(RoundedRectangle(cornerRadius: Radius.largeButton))
            if let error = folders.error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("settings.folders.error")
            }
        }
    }

    @ViewBuilder private var folderList: some View {
        if !connection.isConnected && !folders.isLoaded {
            folderMessage("Watched folders show once the server answers.")
        } else if folders.isLoaded && folders.folders.isEmpty {
            folderMessage("No watched folders. Add one with +.")
        } else {
            List(selection: $selection) {
                ForEach(folders.folders, id: \.id) { folder in
                    FolderRow(folder: folder, connected: connection.isConnected)
                        .tag(folder.id)
                        .listRowSeparator(.visible)
                        .listRowInsets(EdgeInsets(top: 0, leading: 10, bottom: 0, trailing: 10))
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.watchedFolder)
            .frame(height: RowHeight.watchedFolder * CGFloat(min(max(folders.folders.count, 3), 6)))
            .onDeleteCommand(perform: removeSelected)
            .accessibilityLabel("Watched folders")
            .accessibilityIdentifier("settings.folders")
        }
    }

    private func folderMessage(_ text: LocalizedStringKey) -> some View {
        Text(text)
            .font(Typography.secondary)
            .foregroundStyle(Palette.text3)
            .frame(maxWidth: .infinity, minHeight: RowHeight.watchedFolder * 3)
            .accessibilityIdentifier("settings.folders.message")
    }

    private var folderButtons: some View {
        HStack(spacing: 0) {
            Button {
                WatchFolderPanel.run(folders: folders, connection: connection, window: NSApp.keyWindow)
            } label: {
                Image(systemName: "plus").frame(width: 26, height: 22).contentShape(Rectangle())
            }
            .disabled(!connection.isConnected)
            .help("Add folder")
            .accessibilityLabel("Add folder")
            .accessibilityIdentifier("settings.folders.add")
            HairlineDivider(axis: .vertical).frame(height: 14)
            Button(action: removeSelected) {
                Image(systemName: "minus").frame(width: 26, height: 22).contentShape(Rectangle())
            }
            .disabled(selection == nil || !connection.isConnected)
            .help("Remove selected folder")
            .accessibilityLabel("Remove selected folder")
            .accessibilityIdentifier("settings.folders.remove")
            Spacer(minLength: 0)
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, 4)
        .background(Palette.side)
    }

    private func removeSelected() {
        guard let id = selection else { return }
        selection = nil
        Task { await folders.remove(id) }
    }
}

/// A watched folder: enable checkbox, mono path, status ("last import 21:02", "idle",
/// "paused"; the row in `--text3` while paused).
private struct FolderRow: View {
    let folder: Components.Schemas.WatchDirRead
    let connected: Bool

    @Environment(WatchFolders.self) private var folders

    var body: some View {
        HStack(spacing: 10) {
            Toggle(
                "Watch \(folder.displayPath)",
                isOn: Binding(
                    get: { folder.enabled },
                    set: { enabled in Task { await folders.setEnabled(enabled, for: folder.id) } })
            )
            .toggleStyle(.checkbox)
            .labelsHidden()
            .disabled(!connected)
            .accessibilityIdentifier("settings.folder.\(folder.id).enabled")
            Text(folder.displayPath)
                .font(Typography.mono)
                .foregroundStyle(folder.enabled ? Palette.text : Palette.text3)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(folder.hostPath ?? folder.path)
            Spacer(minLength: 8)
            Text(folder.statusText())
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .monospacedDigit()
                .accessibilityIdentifier("settings.folder.\(folder.id).status")
        }
        .frame(minHeight: RowHeight.watchedFolder)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(folder.displayPath)
        .accessibilityIdentifier("settings.folder.\(folder.id)")
    }
}

/// "● Connected" (`--ok`), "● Not reachable" (`--danger`) or "Connecting…".
private struct ConnectionBadge: View {
    @Environment(ServerConnection.self) private var connection

    var body: some View {
        HStack(spacing: 6) {
            switch connection.state {
            case .connecting:
                ProgressView().controlSize(.mini)
                Text("Connecting…").foregroundStyle(Palette.text3)
            case .connected:
                Circle().fill(Palette.ok).frame(width: 8, height: 8)
                Text("Connected").foregroundStyle(Palette.ok)
            case .unreachable:
                Circle().fill(Palette.danger).frame(width: 8, height: 8)
                Text("Not reachable").foregroundStyle(Palette.danger)
            }
        }
        .font(Typography.secondary)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("settings.connectionStatus")
    }
}
