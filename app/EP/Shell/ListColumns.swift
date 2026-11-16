import EPAPI
import SwiftUI

/// The three list columns (Library: artists, release groups, tracks — §3.2–3.4; Radio:
/// shows, episodes, tracklist — §6). Placeholders until stages 3 and 7 fill them.
struct ListColumns: View {
    let section: AppSection

    var body: some View {
        ColumnGrid(spec: section.columns) {
            switch section {
            case .library:
                Column(id: "artists", label: "Artists", background: Palette.side) {
                    ConnectionStatusText()
                }
                Column(id: "releaseGroups", label: "Albums", header: true) {}
                Column(id: "tracks", label: "Tracks", header: true, trailingBorder: false) {}
            case .radio:
                Column(id: "shows", label: "Shows", background: Palette.side) {}
                Column(id: "episodes", label: "Episodes", header: true) {}
                Column(id: "tracklist", label: "Tracklist", header: true, trailingBorder: false) {}
            }
        }
    }
}

/// One list column: an optional 32 pt header, content, and a 1 pt right border.
private struct Column<Content: View>: View {
    let id: String
    let label: LocalizedStringKey
    var background = Palette.bg
    var header = false
    var trailingBorder = true
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            if header {
                HStack {}
                    .frame(maxWidth: .infinity)
                    .frame(height: Metrics.columnHeaderHeight)
                    .overlay(alignment: .bottom) { HairlineDivider() }
            }
            ZStack { content }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(background)
        .overlay(alignment: .trailing) {
            if trailingBorder { HairlineDivider(axis: .vertical) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(label)
        .accessibilityIdentifier("\(id).column")
    }
}

/// What the connection is doing, in the artists column until it has artists to show.
private struct ConnectionStatusText: View {
    @Environment(ServerConnection.self) private var connection

    var body: some View {
        Group {
            switch connection.state {
            case .connecting:
                Text("Connecting to \(connection.serverURL.hostAndPort)…")
            case .connected(let server):
                Text("Connected to \(connection.serverURL.hostAndPort) · EP \(server.version)")
            case .unreachable:
                Text("Not connected")
            }
        }
        .font(Typography.secondary)
        .foregroundStyle(Palette.text3)
        .multilineTextAlignment(.center)
        .padding()
        .accessibilityIdentifier("connection.status")
    }
}

/// A 1 pt `--line` separator.
struct HairlineDivider: View {
    var axis: Axis = .horizontal

    var body: some View {
        Rectangle()
            .fill(Palette.line)
            .frame(
                width: axis == .vertical ? Metrics.hairline : nil,
                height: axis == .horizontal ? Metrics.hairline : nil
            )
            .accessibilityHidden(true)
    }
}
