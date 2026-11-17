import SwiftUI

/// "Can’t reach host:port. Retrying in 8 s." under the toolbar (§11.2, decision 12), with
/// Retry and Settings…. What was loaded stays visible behind it (dimmed by the window);
/// notes are read-only and server files do not play until the server answers again.
struct UnreachableBanner: View {
    @Environment(ServerConnection.self) private var connection

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 16))
                .foregroundStyle(Palette.danger)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text("Can’t reach \(connection.serverURL.hostAndPort).")
                        .font(Typography.name)
                        .foregroundStyle(Palette.text)
                    countdown
                        .font(Typography.body)
                        .monospacedDigit()
                        .foregroundStyle(Palette.text2)
                        .accessibilityIdentifier("connection.countdown")
                }
                .lineLimit(1)
                Text(
                    "Showing what was last loaded. Editing and playback of server files are paused; notes are read-only until reconnected."
                )
                .font(Typography.secondary)
                .foregroundStyle(Palette.text2)
                .lineLimit(1)
                .truncationMode(.tail)
            }
            Spacer(minLength: 0)
            Button("Retry") {
                Task { await connection.connect() }
            }
            .controlSize(.small)
            .disabled(connection.state.isConnecting)
            .accessibilityIdentifier("connection.retry")
            SettingsLink {
                Text("Settings…")
            }
            .controlSize(.small)
            .accessibilityIdentifier("connection.settings")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(Palette.chip)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Server unreachable")
        .accessibilityIdentifier("connection.banner")
    }

    /// "Retrying in 8 s.", "Retrying…" while an attempt runs.
    @ViewBuilder private var countdown: some View {
        if let retryAt = connection.retryAt {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let seconds = max(0, Int(retryAt.timeIntervalSince(context.date).rounded(.up)))
                Text("Retrying in \(seconds) s.")
            }
        } else if connection.state.isConnecting {
            Text("Retrying…")
        }
    }
}

extension ServerConnection.State {
    var isConnecting: Bool {
        if case .connecting = self { true } else { false }
    }
}
