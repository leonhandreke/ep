import AppKit
import EPAPI
import SwiftUI

/// The search palette (§8, ⌘K and ⌘F, decision 9): a 640 pt card 56 pt below the top of
/// the window, over its content. Typing searches the library; results are grouped (TOP
/// HIT, TRACKS, ALBUMS, ARTISTS, RADIO) with covers. ↑↓ choose, ↩ opens, ⌘↩ plays, ⌥↩
/// plays next, ⌘C copies the link, esc (or a click outside) closes. A tag click opens it
/// filtered to that tag (decision 7); ⌫ in the empty field removes the filter.
struct SearchPalette: View {
    let store: SearchStore
    let close: () -> Void

    @Environment(\.openEntity) private var openEntity
    @Environment(Player.self) private var player
    @State private var text = ""

    static let width: CGFloat = 640

    var body: some View {
        ZStack(alignment: .top) {
            // A click outside the card closes the palette.
            Color.black.opacity(0.001)
                .onTapGesture(perform: close)
                .accessibilityHidden(true)
            card
                .padding(.top, 56)
        }
        .onAppear { text = store.query }
    }

    private var card: some View {
        VStack(spacing: 0) {
            inputRow
            results
            footer
        }
        .frame(width: Self.width)
        .background(Palette.bg, in: RoundedRectangle(cornerRadius: Radius.sheet))
        .clipShape(RoundedRectangle(cornerRadius: Radius.sheet))
        .popoverShadow()
        .background(shortcuts)
        .accessibilityElement(children: .contain)
        .accessibilityAddTraits(.isModal)
        .accessibilityLabel("Search")
        .accessibilityIdentifier("search.palette")
    }

    // MARK: - Input

    private var inputRow: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 18))
                .foregroundStyle(Palette.text3)
                .accessibilityHidden(true)
            if let tag = store.tag {
                HStack(spacing: 4) {
                    Text(verbatim: "#\(tag.name)").font(Typography.body)
                    Button {
                        store.filter(by: nil)
                    } label: {
                        Image(systemName: "xmark").font(.system(size: 9, weight: .semibold))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Remove filter \(tag.name)")
                    .accessibilityIdentifier("search.tag.remove")
                }
                .foregroundStyle(Palette.text)
                .padding(.leading, 10)
                .padding(.trailing, 8)
                .frame(height: 24)
                .background(Palette.chip, in: Capsule())
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("search.tag")
            }
            PaletteTextField(
                text: $text, placeholder: store.tag == nil ? String(localized: "Search") : String(localized: "Search in tag"),
                onCommand: command
            )
            .frame(height: 24)
            .onChange(of: text) { _, new in store.update(new) }
            Text("esc")
                .font(Typography.meta)
                .foregroundStyle(Palette.text3)
                .accessibilityHidden(true)
        }
        .padding(.horizontal, 16)
        .frame(height: 52)
        .overlay(alignment: .bottom) { HairlineDivider() }
    }

    // MARK: - Results

    @ViewBuilder private var results: some View {
        if store.hits.isEmpty {
            Text(emptyText)
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .frame(maxWidth: .infinity, minHeight: 56)
                .accessibilityIdentifier("search.empty")
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(store.groups) { group in
                            Text(group.kind.title)
                                .sectionLabelStyle()
                                .padding(.horizontal, 10)
                                .padding(.top, 8)
                                .padding(.bottom, 4)
                                .accessibilityAddTraits(.isHeader)
                                .accessibilityIdentifier("search.group.\(group.kind.rawValue)")
                            ForEach(group.hits) { hit in
                                row(hit)
                            }
                        }
                    }
                    .padding(6)
                }
                .frame(height: resultsHeight)
                .onChange(of: store.selection) { _, _ in
                    if let selected = store.selected { proxy.scrollTo(selected.id) }
                }
            }
        }
    }

    /// The results' height: their content, at most 470 (then they scroll).
    private var resultsHeight: CGFloat {
        let content = CGFloat(store.groups.count) * 26 + CGFloat(store.hits.count) * RowHeight.searchHit + 12
        return min(content, 470)
    }

    private var emptyText: String {
        if store.isEmpty { return String(localized: "Type to search artists, albums, tracks and radio.") }
        if store.isSearching { return String(localized: "Searching…") }
        if store.failed { return String(localized: "Couldn’t search.") }
        return String(localized: "Nothing matches.")
    }

    private func row(_ hit: EntitySummary) -> some View {
        let index = store.hits.firstIndex(of: hit) ?? 0
        let selected = index == store.selection
        return EntityHitRow(hit: hit, selected: selected, id: "search.row.\(index)")
            .background(selected ? Palette.sel : .clear, in: RoundedRectangle(cornerRadius: Radius.rowLarge))
            .contentShape(Rectangle())
            .onTapGesture {
                store.select(hit)
                open(hit)
            }
            .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
            .id(hit.id)
    }

    private var footer: some View {
        HStack(spacing: 16) {
            Text("↩ open")
            Text("⌘↩ play")
            Text("⌥↩ play next")
            Text("⌘C copy link")
            Spacer(minLength: 0)
            Text("Matches ignore accents and small typos")
        }
        .font(Typography.meta)
        .foregroundStyle(Palette.text2)
        .padding(.horizontal, 14)
        .frame(height: 32)
        .background(Palette.side)
        .overlay(alignment: .top) { HairlineDivider() }
        .accessibilityHidden(true)
    }

    /// ⌘↩ and ⌘C as key equivalents of the window, ahead of the Edit menu's Copy.
    private var shortcuts: some View {
        ZStack {
            Button("Play") { if let hit = store.selected { play(hit, next: false) } }
                .keyboardShortcut(.return, modifiers: .command)
            Button("Copy Link") { if let hit = store.selected { copy(hit) } }
                .keyboardShortcut("c", modifiers: .command)
        }
        .opacity(0)
        .frame(width: 0, height: 0)
        .accessibilityHidden(true)
    }

    // MARK: - Actions

    private func command(_ command: PaletteTextField.Command) -> Bool {
        switch command {
        case .up: store.move(-1)
        case .down: store.move(1)
        case .open: if let hit = store.selected { open(hit) }
        case .play: if let hit = store.selected { play(hit, next: false) }
        case .playNext: if let hit = store.selected { play(hit, next: true) }
        case .cancel: close()
        case .deleteAtStart:
            guard store.tag != nil else { return false }
            store.filter(by: nil)
        }
        return true
    }

    private func open(_ hit: EntitySummary) {
        close()
        openEntity?(
            OpenTarget(entity: hit.ref, radioEntryID: hit.radioEntryID),
            newWindow: NSEvent.modifierFlags.contains([.command, .option]))
    }

    private func play(_ hit: EntitySummary, next: Bool) {
        guard Player.plays(hit.ref.type) else { return NSSound.beep() }
        player.play(hit.ref, from: hit.radioEntryID, next: next)
        close()
    }

    private func copy(_ hit: EntitySummary) {
        copyNoteLink(hit.link)
        close()
    }
}

/// The palette's 17 pt input: an `NSTextField` whose field editor hands ↑ ↓ ↩ ⌥↩ ⎋ and ⌫
/// at the start to the palette (a SwiftUI field cannot tell ⌥↩ from ↩).
struct PaletteTextField: NSViewRepresentable {
    enum Command {
        case up, down, open, play, playNext, cancel, deleteAtStart
    }

    @Binding var text: String
    let placeholder: String
    /// True when the palette handled the command.
    let onCommand: (Command) -> Bool

    func makeNSView(context: Context) -> NSTextField {
        let field = NSTextField()
        field.isBordered = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.font = .systemFont(ofSize: 17)
        field.cell?.lineBreakMode = .byTruncatingTail
        field.cell?.isScrollable = true
        field.delegate = context.coordinator
        field.setAccessibilityLabel(String(localized: "Search library"))
        field.setAccessibilityIdentifier("search.field")
        DispatchQueue.main.async { field.window?.makeFirstResponder(field) }
        return field
    }

    func updateNSView(_ field: NSTextField, context: Context) {
        context.coordinator.parent = self
        if field.stringValue != text { field.stringValue = text }
        field.placeholderString = placeholder
    }

    func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: PaletteTextField

        init(parent: PaletteTextField) { self.parent = parent }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSTextField else { return }
            parent.text = field.stringValue
        }

        func control(_ control: NSControl, textView: NSTextView, doCommandBy selector: Selector) -> Bool {
            let flags = NSApp.currentEvent?.modifierFlags ?? []
            switch selector {
            case #selector(NSResponder.moveUp(_:)): return parent.onCommand(.up)
            case #selector(NSResponder.moveDown(_:)): return parent.onCommand(.down)
            case #selector(NSResponder.insertNewline(_:)):
                return parent.onCommand(flags.contains(.command) ? .play : flags.contains(.option) ? .playNext : .open)
            case #selector(NSResponder.insertNewlineIgnoringFieldEditor(_:)): return parent.onCommand(.playNext)
            case #selector(NSResponder.cancelOperation(_:)): return parent.onCommand(.cancel)
            case #selector(NSResponder.deleteBackward(_:)):
                guard textView.selectedRange() == NSRange(location: 0, length: 0) else { return false }
                return parent.onCommand(.deleteAtStart)
            default: return false
            }
        }
    }
}
