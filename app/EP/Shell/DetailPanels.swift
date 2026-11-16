import EPAPI
import SwiftUI

/// The detail panels under the lists (§3.7, decision 1). Library: three panels side by
/// side on the column grid (artist, album, track), each with stacked Note / Tags /
/// Linked from sections. Radio: one full-width panel for the episode (tabs: stage 7).
/// Collapsed, only the panel headers stay, so the chevron can expand them again.
struct DetailPanels: View {
    let section: AppSection
    @Binding var collapsed: Bool
    @Environment(LibraryBrowser.self) private var browser

    var body: some View {
        switch section {
        case .library:
            ColumnGrid(spec: .library) {
                DetailPanel(id: "artist", badge: "Artist", title: browser.artist?.name, collapsed: $collapsed)
                DetailPanel(id: "album", badge: "Album", title: albumTitle, collapsed: $collapsed)
                DetailPanel(
                    id: "track", badge: "Track", title: browser.selectedTrack?.title, collapsed: $collapsed,
                    trailingBorder: false)
            }
        case .radio:
            DetailPanel(id: "episode", badge: "Episode", collapsed: $collapsed, trailingBorder: false)
        }
    }

    private var albumTitle: String? {
        switch browser.location.album {
        case .releaseGroup(let id): browser.releaseGroup.flatMap { $0.id == id ? $0.title : nil }
        case .looseTracks: String(localized: "Loose tracks")
        case nil: nil
        }
    }
}

private struct DetailPanel: View {
    let id: String
    let badge: LocalizedStringKey
    var title: String?
    @Binding var collapsed: Bool
    var trailingBorder = true

    var body: some View {
        VStack(spacing: 0) {
            header
            if !collapsed {
                ScrollView {}
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .background(Palette.bg)
        .overlay(alignment: .trailing) {
            if trailingBorder { HairlineDivider(axis: .vertical) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(Text(badge))
        .accessibilityIdentifier("detail.\(id)")
    }

    private var header: some View {
        HStack(spacing: 8) {
            KindBadge(label: badge)
            if let title {
                Text(title)
                    .font(Typography.name)
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .accessibilityIdentifier("detail.\(id).title")
            }
            Spacer(minLength: 0)
            Button {
                collapsed.toggle()
            } label: {
                Image(systemName: collapsed ? "chevron.up" : "chevron.down")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Palette.text3)
                    .frame(width: Metrics.collapseButton, height: Metrics.collapseButton)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(collapsed ? "Expand panels" : "Collapse panels")
            .accessibilityLabel(collapsed ? "Expand detail panels (⌥⌘D)" : "Collapse detail panels (⌥⌘D)")
            .accessibilityIdentifier("detail.\(id).collapse")
        }
        .padding(.leading, 14)
        .padding(.trailing, 8)
        .frame(height: Metrics.panelHeaderHeight)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .overlay(alignment: .top) {
            if collapsed { HairlineDivider() }
        }
    }
}

/// ARTIST, ALBUM, TRACK, EPISODE, SHOW: 10 pt bold uppercase on `--chip`.
struct KindBadge: View {
    let label: LocalizedStringKey

    var body: some View {
        Text(label)
            .font(Typography.badge)
            .tracking(Typography.badgeTracking)
            .textCase(.uppercase)
            .foregroundStyle(Palette.text2)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Palette.chip, in: RoundedRectangle(cornerRadius: Radius.chip))
    }
}

/// The one divider above all detail panels: 7 pt, `--side`, borders, centred grabber;
/// drag to resize. VoiceOver adjusts it with increment/decrement.
struct DetailDivider: View {
    let height: CGFloat
    let available: CGFloat
    let setHeight: (CGFloat) -> Void

    @State private var dragStartHeight: CGFloat?

    var body: some View {
        Palette.side
            .frame(height: Metrics.dividerHeight)
            .overlay(alignment: .top) { HairlineDivider() }
            .overlay(alignment: .bottom) { HairlineDivider() }
            .overlay {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Palette.line)
                    .frame(width: Metrics.dividerGrabber.width, height: Metrics.dividerGrabber.height)
            }
            .contentShape(Rectangle())
            .pointerStyle(.rowResize)
            .gesture(
                DragGesture(minimumDistance: 1, coordinateSpace: .global)
                    .onChanged { value in
                        let start = dragStartHeight ?? height
                        dragStartHeight = start
                        setHeight(start - value.translation.height)
                    }
                    .onEnded { _ in dragStartHeight = nil }
            )
            .accessibilityElement()
            .accessibilityLabel("Detail panel height")
            .accessibilityValue("\(Int(height)) points")
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: setHeight(height + 40)
                case .decrement: setHeight(height - 40)
                @unknown default: break
                }
            }
            .accessibilityIdentifier("detail.divider")
    }
}
