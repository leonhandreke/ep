import EPAPI
import SwiftUI

/// Settings (§10), the server part only for now: address, connection status, server
/// version and counts. Library location and watched folders come with stage 10.
struct SettingsView: View {
    @Environment(AppSettings.self) private var settings
    @Environment(ServerConnection.self) private var connection
    @State private var address = ""
    @State private var isInvalid = false

    var body: some View {
        Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 8) {
            GridRow {
                Text("Server")
                    .foregroundStyle(Palette.text2)
                    .gridColumnAlignment(.trailing)
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
                        if case .unreachable = connection.state {
                            Button("Retry") { Task { await connection.connect() } }
                                .controlSize(.small)
                                .accessibilityIdentifier("settings.retry")
                        }
                    }
                    if isInvalid {
                        Text("Enter an address like http://localhost:8000.")
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.danger)
                    } else if settings.isServerURLOverridden {
                        Text("Set for this run by EP_SERVER_URL or -serverURL.")
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.text3)
                    }
                    if case .connected(let server) = connection.state {
                        Text(
                            "Server \(server.version) · \(server.counts.artists.formatted()) artists · \(server.counts.tracks.formatted()) tracks"
                        )
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text2)
                        .accessibilityIdentifier("settings.serverInfo")
                    }
                }
            }
        }
        .font(Typography.body)
        .padding(24)
        .frame(width: 720, alignment: .topLeading)
        .onAppear { address = settings.serverURL.absoluteString }
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
