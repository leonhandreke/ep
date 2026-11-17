import AppKit
import SwiftUI

/// The toolbar's Import button (design §3.1, board 11): a plus in a ring whose accent arc
/// is the running job's progress, and a badge: the number of jobs waiting for review
/// (`--warn`) or "!" for a failed job (`--danger`). A plain view with a tap, not a
/// `Button`: a custom-label button collapses to nothing in a macOS toolbar item.
struct ImportToolbarButton: View {
    let action: () -> Void

    @Environment(ImportStore.self) private var imports

    var body: some View {
        ZStack {
            ring
            Image(systemName: "plus")
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(Palette.text2)
        }
        .frame(width: 22, height: 22)
        .frame(width: Metrics.importButton.width, height: Metrics.importButton.height)
        .overlay(alignment: .topTrailing) { badge.offset(x: 2) }
        .contentShape(Rectangle())
        .onTapGesture(perform: action)
        .help("Import (⇧⌘I)")
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { action() }
        .accessibilityLabel(imports.accessibilitySummary)
        .accessibilityValue(imports.progress.map { "\(Int($0 * 100)) %" } ?? "")
        .accessibilityIdentifier("toolbar.import")
    }

    private var ring: some View {
        ZStack {
            Circle().strokeBorder(Palette.line, lineWidth: 2)
            if let progress = imports.progress {
                Circle()
                    .inset(by: 1)
                    .trim(from: 0, to: max(progress, 0.04))
                    .stroke(Palette.accent, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .animation(.easeInOut(duration: 0.3), value: progress)
            }
        }
    }

    @ViewBuilder private var badge: some View {
        switch imports.badge {
        case .none:
            EmptyView()
        case .review(let count):
            BadgeView(text: count > 99 ? "99+" : String(count), color: Palette.warn)
        case .failed:
            BadgeView(text: "!", color: Palette.danger)
        }
    }
}

/// Min width 15, height 15, radius 8, white 10 pt bold.
private struct BadgeView: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .foregroundStyle(Palette.badgeText)
            .padding(.horizontal, 4)
            .frame(minWidth: 15, minHeight: 15)
            .background(color, in: Capsule())
    }
}

/// The toast (board 11, 7.3): "Late Night Tapes is ready for review" with Review, or a
/// job done or failed with Show. Bottom right of the window, above the player bar, for
/// eight seconds (the design gives neither place nor time).
struct ImportToast: View {
    let toast: ImportStore.Toast
    let open: () -> Void

    @Environment(ImportStore.self) private var imports

    var body: some View {
        HStack(spacing: 10) {
            CoverThumbnail(path: toast.coverURL, size: 30, radius: 15)
            VStack(alignment: .leading, spacing: 1) {
                Text(toast.title).font(Typography.name).lineLimit(1)
                if !toast.message.isEmpty {
                    Text(toast.message)
                        .font(Typography.secondary)
                        .foregroundStyle(toast.kind == .failed ? Palette.danger : Palette.text2)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: 300, alignment: .leading)
            Button(toast.kind == .review ? "Review" : "Show", action: open)
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .accessibilityIdentifier("import.toast.open")
            Button("Close", systemImage: "xmark") { imports.dismissToast() }
                .labelStyle(.iconOnly)
                .buttonStyle(.plain)
                .foregroundStyle(Palette.text3)
                .accessibilityLabel("Close")
                .accessibilityIdentifier("import.toast.close")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(Palette.bg, in: RoundedRectangle(cornerRadius: Radius.card))
        .modifier(PopoverShadow())
        .accessibilityElement(children: .contain)
        .accessibilityLabel(toast.title)
        .accessibilityAddTraits(.updatesFrequently)
        .accessibilityIdentifier("import.toast")
    }
}

/// ⌘V anywhere in the main window (§12): with a web link on the pasteboard, and no text
/// field taking the paste, opens the import sheet with the link. A key-down monitor on
/// this view's window, like Space for playback (`PlaybackKeys`).
struct PasteLinkMonitor: NSViewRepresentable {
    let paste: (String) -> Void

    func makeNSView(context: Context) -> MonitorView {
        let view = MonitorView()
        view.paste = paste
        return view
    }

    func updateNSView(_ view: MonitorView, context: Context) {
        view.paste = paste
    }

    final class MonitorView: NSView {
        var paste: ((String) -> Void)?
        private var monitor: Any?

        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            if let monitor { NSEvent.removeMonitor(monitor) }
            monitor = nil
            guard window != nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
                guard let self, self.handles(event) else { return event }
                return nil
            }
        }

        private func handles(_ event: NSEvent) -> Bool {
            guard let window, event.window === window, window.attachedSheet == nil,
                event.modifierFlags.intersection([.command, .option, .control, .shift]) == .command,
                event.charactersIgnoringModifiers == "v",
                !(window.firstResponder is NSText),
                let link = Self.link(on: .general)
            else { return false }
            paste?(link)
            return true
        }

        /// A web link on the pasteboard (a URL, or text that is one).
        static func link(on pasteboard: NSPasteboard) -> String? {
            let text =
                (pasteboard.readObjects(forClasses: [NSURL.self]) as? [URL])?.first?.absoluteString
                ?? pasteboard.string(forType: .string)
            guard let text = text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.contains(where: \.isNewline),
                let url = URL(string: text), ["http", "https"].contains(url.scheme?.lowercased() ?? ""), url.host != nil
            else { return nil }
            return text
        }
    }
}

extension EnvironmentValues {
    /// Opens the import sheet of this main window, with a link to start from (set by
    /// `MainWindow`): Import… buttons, ⌥↩ on a link-only track.
    @Entry var openImport: ((String?) -> Void)?
}
