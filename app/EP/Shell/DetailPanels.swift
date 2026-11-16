import EPAPI
import SwiftUI

/// The detail panels under the lists (§3.7, decision 1). Library: three panels side by
/// side on the column grid (artist, album, track), each with stacked Note / Tags /
/// Linked from sections. Radio: one full-width tabbed panel for the episode, or the show
/// when no episode is selected (`RadioDetailPanel`).
/// Collapsed, only the panel headers stay, so the chevron can expand them again.
struct DetailPanels: View {
    let section: AppSection
    @Binding var collapsed: Bool
    let artist: EntityDetails
    let album: EntityDetails
    let track: EntityDetails
    let radio: EntityDetails
    @Environment(LibraryBrowser.self) private var browser

    var body: some View {
        switch section {
        case .library:
            ColumnGrid(spec: .library) {
                DetailPanel(id: "artist", badge: "Artist", title: browser.artist?.name, collapsed: $collapsed) {
                    DetailPanelBody(details: artist, id: "detail.artist", kind: .artist(name: browser.artist?.name))
                }
                DetailPanel(id: "album", badge: "Album", title: albumTitle, collapsed: $collapsed) {
                    if browser.location.album == .looseTracks {
                        LooseTracksHint()
                    } else {
                        DetailPanelBody(details: album, id: "detail.album", kind: .album)
                    }
                }
                DetailPanel(
                    id: "track", badge: "Track", title: browser.selectedTrack?.title, collapsed: $collapsed,
                    trailingBorder: false
                ) {
                    DetailPanelBody(details: track, id: "detail.track", kind: .track)
                }
            }
        case .radio:
            RadioDetailPanel(details: radio, collapsed: $collapsed)
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

/// The album panel for "Loose tracks", which is no entity: nothing to note or tag.
private struct LooseTracksHint: View {
    var body: some View {
        Text("Recordings on no release. Their notes and tags are in the track panel.")
            .font(Typography.secondary)
            .foregroundStyle(Palette.text3)
            .padding(.horizontal, 18)
            .padding(.top, 14)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}

private struct DetailPanel<Content: View>: View {
    let id: String
    let badge: LocalizedStringKey
    var title: String?
    @Binding var collapsed: Bool
    var trailingBorder = true
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) {
            header
            if !collapsed {
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
            CollapsePanelsButton(id: "detail.\(id).collapse", collapsed: $collapsed)
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

/// The chevron that collapses and expands the detail panels (⌥⌘D).
struct CollapsePanelsButton: View {
    let id: String
    @Binding var collapsed: Bool

    var body: some View {
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
        .accessibilityIdentifier(id)
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
