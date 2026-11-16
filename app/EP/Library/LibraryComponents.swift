import EPAPI
import SwiftUI

/// The three Library list columns, for focus and arrow-key movement between them.
enum LibraryColumn: Hashable {
    case artists, releaseGroups, tracks
}

/// The 14 pt play-state icon (§3.5): its shape carries the state, colour only adds to it
/// (radio excerpt in `--warn`, file missing in `--danger`).
struct PlayStateIcon: View {
    let state: Components.Schemas.PlayState
    /// On a focused selection everything is `--seltext`.
    var emphasized = false

    var body: some View {
        icon
            .frame(width: Metrics.playStateIcon, height: Metrics.playStateIcon)
            .foregroundStyle(emphasized ? Palette.selText : color)
            .accessibilityElement()
            .accessibilityLabel(state.label)
    }

    private var color: Color {
        switch state {
        case .radioExcerpt: Palette.warn
        case .fileMissing: Palette.danger
        default: Palette.text3
        }
    }

    @ViewBuilder private var icon: some View {
        switch state {
        case .file:
            // Filled dot, r3.
            Circle().frame(width: 6, height: 6)
        case .slice:
            // Outlined 11×5 bar with its middle third filled: a range inside one file.
            ZStack {
                RoundedRectangle(cornerRadius: 1.5).strokeBorder(lineWidth: 1.2)
                Rectangle().frame(width: 11 / 3, height: 5)
            }
            .frame(width: 11, height: 5)
        case .radioExcerpt:
            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 10, weight: .medium))
        case .linkOnly:
            Image(systemName: "arrow.up.right").font(.system(size: 9, weight: .semibold))
        case .noSource:
            // Dashed circle, r4.
            Circle().stroke(style: StrokeStyle(lineWidth: 1.2, dash: [2, 2])).frame(width: 8, height: 8)
        case .fileMissing:
            Image(systemName: "exclamationmark.circle").font(.system(size: 11, weight: .medium))
        }
    }
}

/// A list column's 32 pt header: a title (600) and meta (12 pt `--text3`), with room for
/// a trailing control.
struct ColumnHeader<Trailing: View>: View {
    let id: String
    let title: String?
    let meta: String?
    var horizontalPadding: CGFloat = 12
    var metaTrailing = true
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: 8) {
            if let title {
                Text(title)
                    .font(Typography.name)
                    .foregroundStyle(Palette.text)
                    .lineLimit(1)
                    .layoutPriority(1)
            }
            if !metaTrailing { metaText }
            Spacer(minLength: 0)
            if metaTrailing { metaText }
            trailing
        }
        .padding(.horizontal, horizontalPadding)
        .frame(maxWidth: .infinity)
        .frame(height: Metrics.columnHeaderHeight)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel([title, meta].compactMap(\.self).joined(separator: " · "))
        .accessibilityIdentifier("\(id).header")
    }

    @ViewBuilder private var metaText: some View {
        if let meta {
            Text(meta)
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .monospacedDigit()
                .lineLimit(1)
        }
    }
}

extension ColumnHeader where Trailing == EmptyView {
    init(id: String, title: String?, meta: String?) {
        self.init(id: id, title: title, meta: meta) { EmptyView() }
    }
}

/// Loading skeleton rows (§11.3), shown only once a load has taken longer than
/// `Metrics.skeletonDelay`, so fast loads never flash.
struct DelayedSkeleton: View {
    var rowHeight: CGFloat
    var cover = false
    var rows = 8

    @State private var visible = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if visible {
                ForEach(0..<rows, id: \.self) { index in
                    SkeletonRow(index: index, cover: cover).frame(height: rowHeight)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, Metrics.listInset + Metrics.rowPadding)
        .padding(.top, Metrics.listInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .task {
            try? await Task.sleep(for: Metrics.skeletonDelay)
            visible = true
        }
        .accessibilityElement()
        .accessibilityLabel("Loading")
        .accessibilityIdentifier("skeleton")
    }
}

private struct SkeletonRow: View {
    let index: Int
    let cover: Bool

    private static let widths: [(CGFloat, CGFloat)] = [(0.7, 0.44), (0.5, 0.3), (0.62, 0.38), (0.4, 0.26)]

    var body: some View {
        let (first, second) = Self.widths[index % Self.widths.count]
        GeometryReader { geometry in
            HStack(spacing: 10) {
                if cover {
                    RoundedRectangle(cornerRadius: Radius.coverSmall)
                        .fill(Palette.skel)
                        .frame(width: Metrics.coverSmall, height: Metrics.coverSmall)
                }
                VStack(alignment: .leading, spacing: 5) {
                    RoundedRectangle(cornerRadius: 3).fill(Palette.skel)
                        .frame(width: geometry.size.width * first, height: cover ? 10 : 8)
                    if cover {
                        RoundedRectangle(cornerRadius: 3).fill(Palette.skel)
                            .frame(width: geometry.size.width * second, height: 8)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
    }
}

/// A centred message in an empty or failed column, with an optional Retry.
struct ColumnMessage: View {
    let id: String
    let text: String
    var retry: (() -> Void)?

    var body: some View {
        VStack(spacing: 8) {
            Text(text)
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .multilineTextAlignment(.center)
            if let retry {
                Button("Retry", action: retry)
                    .controlSize(.small)
                    .accessibilityIdentifier("\(id).retry")
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("\(id).message")
    }
}

/// A list row's text colours: `--seltext` on the focused column's selection (emphasized),
/// the design's text colours elsewhere. Rows are told, rather than reading the list's
/// background prominence, which SwiftUI does not set for these rows on macOS 15.
struct RowForeground {
    let emphasized: Bool
    var primary: Color { emphasized ? Palette.selText : Palette.text }
    var tertiary: Color { emphasized ? Palette.selText.opacity(0.85) : Palette.text3 }
    var secondary: Color { emphasized ? Palette.selText.opacity(0.9) : Palette.text2 }
}

/// Copies a note link (`[[type:id|Name]]`) to the pasteboard.
@MainActor func copyNoteLink(_ link: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(link, forType: .string)
}
