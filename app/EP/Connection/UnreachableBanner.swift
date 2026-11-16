import SwiftUI

/// "Can't reach host:port." under the toolbar (§11.2, decision 12), with Retry and
/// Settings…. Minimal for now: the retry countdown and dimmed stale content come with
/// stage 10.
struct UnreachableBanner: View {
    @Environment(ServerConnection.self) private var connection

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.circle")
                .font(.system(size: 16))
                .foregroundStyle(Palette.danger)
                .accessibilityHidden(true)
            Text("Can’t reach \(connection.serverURL.hostAndPort).")
                .font(Typography.name)
                .foregroundStyle(Palette.text)
            if case .unreachable(let message) = connection.state {
                Text(message)
                    .font(Typography.body)
                    .foregroundStyle(Palette.text2)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
            Button("Retry") {
                Task { await connection.connect() }
            }
            .controlSize(.small)
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
        .accessibilityIdentifier("connection.banner")
    }
}
