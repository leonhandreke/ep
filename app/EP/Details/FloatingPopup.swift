import AppKit
import SwiftUI

/// A suggestion list that floats over the window below a text field or caret, the way
/// completion lists do: a borderless child window that never takes the keyboard, so
/// typing stays in the field, which forwards ↑↓ ↩ ⎋ to the list. Not clipped by the
/// panel it belongs to. Used by the tag suggestions and the `[[` link suggestions.
final class FloatingPopup {
    private var panel: NSPanel?
    private var hosting: PopupHostingView?

    var isShown: Bool { panel?.isVisible == true }

    /// Shows `content` at `size` below `anchor` (screen coordinates), or above it when
    /// there is no room below.
    func show(_ content: some View, size: CGSize, below anchor: NSRect, in parent: NSWindow) {
        let panel = panel ?? makePanel()
        if let hosting {
            hosting.rootView = AnyView(content)
        } else {
            let hosting = PopupHostingView(rootView: AnyView(content))
            panel.contentView = hosting
            self.hosting = hosting
        }
        // The window has room for the card's shadow around it.
        let inset = PopupCardMetrics.shadowInset
        let screen = parent.screen?.visibleFrame ?? .infinite
        var card = CGPoint(x: anchor.minX, y: anchor.minY - 4 - size.height)
        if card.y < screen.minY { card.y = anchor.maxY + 4 }
        card.x = min(max(card.x, screen.minX), screen.maxX - size.width)
        let frame = NSRect(
            x: card.x - inset, y: card.y - inset, width: size.width + 2 * inset, height: size.height + 2 * inset)
        panel.setFrame(frame, display: true)
        if panel.parent !== parent {
            panel.parent?.removeChildWindow(panel)
            parent.addChildWindow(panel, ordered: .above)
        }
        panel.orderFront(nil)
    }

    func hide() {
        guard let panel else { return }
        panel.parent?.removeChildWindow(panel)
        panel.orderOut(nil)
    }

    private func makePanel() -> NSPanel {
        let panel = PopupPanel(
            contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .popUpMenu
        panel.hidesOnDeactivate = true
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        self.panel = panel
        return panel
    }

    deinit {
        MainActor.assumeIsolated { hide() }
    }
}

/// Takes the click that is the first in a window that is not key (the popup never is).
private final class PopupHostingView: NSHostingView<AnyView> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

private final class PopupPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

/// The popup card: `--bg`, radius, `--shadow`.
struct PopupCard<Content: View>: View {
    var radius: CGFloat = Radius.card
    @ViewBuilder var content: Content

    var body: some View {
        content
            .background(Palette.bg, in: RoundedRectangle(cornerRadius: radius))
            .clipShape(RoundedRectangle(cornerRadius: radius))
            .popoverShadow()
            .padding(PopupCardMetrics.shadowInset)
    }
}

enum PopupCardMetrics {
    /// Room around the card inside the popup window for its shadow.
    static let shadowInset: CGFloat = 16
}

/// Finds the screen frame and window of the view it is the background of (for anchoring
/// a `FloatingPopup` under a SwiftUI field).
struct PopupAnchor: NSViewRepresentable {
    let anchor: (NSView) -> Void

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { anchor(view) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {}
}

extension NSView {
    /// The view's bounds in screen coordinates.
    var screenFrame: NSRect? {
        guard let window else { return nil }
        return window.convertToScreen(convert(bounds, to: nil))
    }
}
