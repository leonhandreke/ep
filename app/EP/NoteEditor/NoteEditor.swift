import AppKit
import EPAPI
import SwiftUI

/// The note editor (§5, board 2): `NoteTextView` in SwiftUI, sized to its text (the panel
/// scrolls, not the editor). Edits go to the `NoteDocument`, which saves them; `[[` opens
/// the link suggestions; ⌘-click on a link opens its target. Read-only while the server
/// cannot be reached (decision 12).
struct NoteEditor: View {
    let document: NoteDocument
    let id: String

    @Environment(ServerConnection.self) private var connection
    @Environment(CoverImages.self) private var covers
    @Environment(\.openEntity) private var openEntity
    @Environment(\.presentSheet) private var presentSheet
    @State private var height: CGFloat = 24

    var body: some View {
        NoteTextRepresentable(
            document: document, id: id, editable: connection.isConnected, connection: connection, covers: covers,
            openEntity: openEntity, presentSheet: presentSheet, height: $height
        )
        .frame(height: height)
        .frame(maxWidth: 720, alignment: .leading)
    }
}

private struct NoteTextRepresentable: NSViewRepresentable {
    let document: NoteDocument
    let id: String
    let editable: Bool
    let connection: ServerConnection
    let covers: CoverImages
    let openEntity: OpenEntityAction?
    let presentSheet: ((WindowSheet) -> Void)?
    @Binding var height: CGFloat

    func makeCoordinator() -> NoteEditorCoordinator {
        NoteEditorCoordinator(connection: connection)
    }

    func makeNSView(context: Context) -> NoteTextView {
        let view = NoteTextView.make()
        let coordinator = context.coordinator
        coordinator.textView = view
        view.delegate = coordinator
        view.placeholder = String(localized: "Write a note… Type [[ to link to anything in the library.")
        view.setAccessibilityLabel(String(localized: "Note"))
        view.setAccessibilityIdentifier("\(id).note.editor")
        view.linkState = { [weak coordinator] link in coordinator?.state(of: link) ?? .unknown }
        view.onOpenLink = { [weak coordinator] index, newWindow, event in
            coordinator?.openLink(index, newWindow: newWindow, event: event)
        }
        view.onOpenWebLink = { NSWorkspace.shared.open($0) }
        view.onResize = { [weak coordinator] in coordinator?.updateHeight() }
        return view
    }

    func updateNSView(_ view: NoteTextView, context: Context) {
        let coordinator = context.coordinator
        coordinator.heightBinding = $height
        coordinator.openEntity = openEntity
        coordinator.presentSheet = presentSheet
        coordinator.covers = covers
        view.isEditable = editable
        if !editable { coordinator.autocomplete.close() }
        coordinator.show(document)
    }

    static func dismantleNSView(_ view: NoteTextView, coordinator: NoteEditorCoordinator) {
        coordinator.popup.hide()
    }
}

/// Connects the text view, the document and the link suggestions.
final class NoteEditorCoordinator: NSObject, NSTextViewDelegate {
    weak var textView: NoteTextView?
    var heightBinding: Binding<CGFloat>?
    var openEntity: OpenEntityAction?
    var presentSheet: ((WindowSheet) -> Void)?
    var covers: CoverImages?
    let autocomplete: LinkAutocomplete
    let popup = FloatingPopup()

    private let connection: ServerConnection
    private var document: NoteDocument?
    private var revision = -1

    init(connection: ServerConnection) {
        self.connection = connection
        autocomplete = LinkAutocomplete(connection: connection)
        super.init()
    }

    /// Takes over the document's text when it is another document or was replaced.
    func show(_ document: NoteDocument) {
        guard let textView else { return }
        let isNew = document !== self.document
        guard isNew || document.revision != revision else { return }
        if isNew {
            self.document?.onTargetsChanged = nil
            self.document = document
            // Restyle when the server tells more about link targets (after a save).
            document.onTargetsChanged = { [weak self] in
                self?.textView?.restyle()
                self?.updateHeight()
            }
        }
        revision = document.revision
        if textView.string != document.text {
            textView.string = document.text
            textView.setSelectedRange(NSRange(location: 0, length: 0))
        }
        textView.resetUndo()
        autocomplete.close()
        popup.hide()
        textView.restyle()
        updateHeight()
    }

    func state(of link: NoteLink) -> NoteTextView.LinkState {
        switch document?.target(of: link.key) {
        case .some(.some(let target)): .resolved(target)
        case .some(.none): .broken
        case .none: .unknown
        }
    }

    // MARK: - Size

    func updateHeight() {
        guard let textView, let layout = textView.textLayoutManager else { return }
        layout.ensureLayout(for: layout.documentRange)
        let height = max(ceil(layout.usageBoundsForTextContainer.height), 20)
        guard let binding = heightBinding, abs(binding.wrappedValue - height) > 0.5 else { return }
        DispatchQueue.main.async { binding.wrappedValue = height }
    }

    // MARK: - Text view delegate

    /// Between `shouldChangeText` and `textDidChange`: the text view is mid-edit, so a
    /// selection change then is restyled once the edit is done.
    private var editing = false

    func textView(_ textView: NSTextView, shouldChangeTextIn range: NSRange, replacementString: String?) -> Bool {
        // A deletion that reaches into a link's hidden `type:uuid|` (Backspace at the start
        // of the display text) would break the link invisibly: it removes the whole link,
        // as deleting a chip does.
        if let view = textView as? NoteTextView, (replacementString ?? "").isEmpty, range.length > 0,
            let link = view.styling.links.first(where: {
                NSIntersectionRange($0.hiddenRange, range).length > 0
                    && !(range.location <= $0.range.location && NSMaxRange(range) >= NSMaxRange($0.range))
            })
        {
            replace(NSUnionRange(range, link.range), with: "")
            return false
        }
        editing = true
        return true
    }

    func textDidChange(_ notification: Notification) {
        editing = false
        guard let textView else { return }
        document?.edit(textView.string)
        textView.restyle()
        updateHeight()
        updateSuggestions()
    }

    func textViewDidChangeSelection(_ notification: Notification) {
        guard let textView, !editing, !textView.hasMarkedText() else { return }
        textView.restyle()
        updateSuggestions()
    }

    func textView(
        _ textView: NSTextView, willChangeSelectionFromCharacterRange oldRange: NSRange,
        toCharacterRange newRange: NSRange
    ) -> NSRange {
        guard newRange.length == 0, let view = textView as? NoteTextView else { return newRange }
        return NSRange(location: view.styling.adjustedCaret(newRange.location, from: oldRange.location), length: 0)
    }

    func undoManager(for view: NSTextView) -> UndoManager? { view.undoManager }

    /// ↑ ↓ ↩ ⇥ ⎋ (and ⌥↩, ⇧↩) go to the suggestions while they are open.
    func textView(_ textView: NSTextView, doCommandBy selector: Selector) -> Bool {
        // Escape would otherwise open the system's word completion.
        guard autocomplete.isOpen else { return selector == #selector(NSResponder.cancelOperation(_:)) }
        let shift = NSApp.currentEvent?.modifierFlags.contains(.shift) == true
        switch selector {
        case #selector(NSResponder.moveUp(_:)):
            autocomplete.move(-1)
        case #selector(NSResponder.moveDown(_:)):
            autocomplete.move(1)
        case #selector(NSResponder.insertNewline(_:)):
            return choose(shift ? .shifted : .accept)
        case #selector(NSResponder.insertNewlineIgnoringFieldEditor(_:)):
            return choose(.alternate)
        case #selector(NSResponder.insertLineBreak(_:)):
            return choose(.shifted)
        case #selector(NSResponder.insertTab(_:)):
            return choose(.acceptAndContinue)
        case #selector(NSResponder.cancelOperation(_:)):
            autocomplete.dismiss()
            popup.hide()
        default:
            return false
        }
        updatePopup()
        return true
    }

    // MARK: - Link suggestions

    private func updateSuggestions() {
        guard let textView, textView.isEditable, textView.window?.firstResponder === textView else {
            autocomplete.close()
            return updatePopup()
        }
        let selection = textView.selectedRange()
        let query =
            selection.length == 0
            ? LinkQuery.find(in: textView.string as NSString, caret: selection.location, styling: textView.styling)
            : nil
        autocomplete.update(query)
        updatePopup()
    }

    private func updatePopup() {
        guard let textView, let window = textView.window, let query = autocomplete.query, let covers else {
            return popup.hide()
        }
        let anchor = textView.firstRect(forCharacterRange: NSRange(location: query.range.location, length: 0), actualRange: nil)
        popup.show(
            LinkSuggestionList(model: autocomplete) { [weak self] index in
                self?.autocomplete.select(index)
                _ = self?.choose(.accept)
            }
            .environment(connection)
            .environment(covers),
            size: LinkSuggestionList.size(for: autocomplete), below: anchor, in: window)
        // The list follows the model (rows arrive asynchronously).
        observePopupSize()
    }

    private var popupObservation = false

    private func observePopupSize() {
        guard !popupObservation else { return }
        popupObservation = true
        withObservationTracking {
            _ = autocomplete.rows
        } onChange: { [weak self] in
            Task { @MainActor in
                self?.popupObservation = false
                if self?.autocomplete.isOpen == true { self?.updatePopup() }
            }
        }
    }

    /// Acts on the highlighted suggestion. False when there is nothing to choose (the key
    /// then does what it normally does).
    private func choose(_ key: LinkAutocomplete.Key) -> Bool {
        guard let query = autocomplete.query, let choice = autocomplete.choice(for: key) else { return false }
        switch choice {
        case .insert(let summary, let continueTyping):
            insertLink(summary.link, target: LinkTarget(ref: summary.ref, title: summary.title), key: summary.linkKey,
                replacing: query, trailingSpace: continueTyping)
        case .newArtist(let name):
            createArtist(name, replacing: query)
        case .newOther(let name):
            present(.create(kinds: [.album, .recording], name: name, credit: []), replacing: query)
        case .searchMusicBrainz(let name):
            present(.musicBrainz(query: name), replacing: query)
        }
        autocomplete.close()
        popup.hide()
        return true
    }

    private func insertLink(
        _ markup: String, target: LinkTarget, key: LinkKey, replacing query: LinkQuery, trailingSpace: Bool
    ) {
        guard let textView else { return }
        document?.remember(target, as: key)
        let text = markup + (trailingSpace ? " " : "")
        guard NSMaxRange(query.range) <= (textView.string as NSString).length,
            textView.shouldChangeText(in: query.range, replacementString: text)
        else { return }
        textView.textStorage?.replaceCharacters(in: query.range, with: text)
        textView.didChangeText()
        textView.setSelectedRange(NSRange(location: query.range.location + (text as NSString).length, length: 0))
    }

    /// "New album or recording…" and "Search MusicBrainz": a sheet; what it adds to the
    /// library is linked in place of the `[[` query.
    private func present(_ kind: WindowSheet.Kind, replacing query: LinkQuery) {
        guard let presentSheet else { return NSSound.beep() }
        presentSheet(
            WindowSheet(kind: kind) { [weak self] created in
                self?.insertCreated(created, replacing: query)
            })
    }

    private func insertCreated(_ created: CreatedEntity, replacing query: LinkQuery) {
        guard let textView, (textView.string as NSString).length >= NSMaxRange(query.range),
            (textView.string as NSString).substring(with: query.range).hasPrefix("[[")
        else { return }
        textView.window?.makeFirstResponder(textView)
        insertLink(
            created.link, target: created.linkTarget, key: created.linkKey, replacing: query, trailingSpace: false)
    }

    /// "+ New artist “…”": creates the artist and links to it.
    private func createArtist(_ name: String, replacing query: LinkQuery) {
        let client = connection.client
        Task {
            do {
                let artist = try await client.createArtist(body: .json(.init(name: name))).created.body.json
                guard let textView, (textView.string as NSString).length >= NSMaxRange(query.range),
                    (textView.string as NSString).substring(with: query.range).hasPrefix("[[")
                else { return }
                insertLink(
                    artist.link, target: LinkTarget(ref: EntityRef(type: .artist, id: artist.id), title: artist.name),
                    key: LinkKey(type: .artist, ref: artist.mbid ?? artist.id), replacing: query, trailingSpace: false)
            } catch {
                connection.reportFailure(error)
                NSSound.beep()
            }
        }
    }

    // MARK: - Opening links

    func openLink(_ index: Int, newWindow: Bool, event: NSEvent) {
        guard let textView, textView.styling.links.indices.contains(index) else { return }
        let link = textView.styling.links[index]
        switch state(of: link) {
        case .broken:
            showBrokenLinkMenu(link, event: event)
        case .resolved(let target):
            openEntity?(OpenTarget(entity: target.ref), newWindow: newWindow)
        case .unknown:
            let client = connection.client
            Task {
                guard
                    let resolved = try? await client.resolveEntity(path: .init(_type: link.type, id: link.ref)).ok.body
                        .json
                else { return NSSound.beep() }
                openEntity?(OpenTarget(entity: EntityRef(type: resolved._type, id: resolved.id)), newWindow: newWindow)
            }
        }
    }

    /// ⌘-click on a broken link: Relink… (the suggestions with the old text) or Remove
    /// link, keep text.
    private func showBrokenLinkMenu(_ link: NoteLink, event: NSEvent) {
        guard let textView else { return }
        menuLink = link
        let menu = NSMenu()
        let relink = NSMenuItem(title: String(localized: "Relink…"), action: #selector(relinkFromMenu), keyEquivalent: "")
        let remove = NSMenuItem(
            title: String(localized: "Remove Link, Keep Text"), action: #selector(removeLinkFromMenu), keyEquivalent: "")
        relink.target = self
        remove.target = self
        relink.setAccessibilityIdentifier("noteEditor.relink")
        remove.setAccessibilityIdentifier("noteEditor.removeLink")
        menu.addItem(relink)
        menu.addItem(remove)
        NSMenu.popUpContextMenu(menu, with: event, for: textView)
    }

    /// The broken link the menu is for.
    private var menuLink: NoteLink?

    @objc private func relinkFromMenu() {
        if let menuLink { relink(menuLink) }
    }

    @objc private func removeLinkFromMenu() {
        if let menuLink { replace(menuLink, with: menuLink.display ?? "") }
    }

    private func relink(_ link: NoteLink) {
        guard let textView else { return }
        replace(link, with: "[[" + (link.display ?? ""))
        textView.window?.makeFirstResponder(textView)
        updateSuggestions()
    }

    private func replace(_ link: NoteLink, with text: String) {
        replace(link.range, with: text)
    }

    private func replace(_ range: NSRange, with text: String) {
        guard let textView, textView.isEditable, NSMaxRange(range) <= (textView.string as NSString).length,
            textView.shouldChangeText(in: range, replacementString: text)
        else { return }
        textView.textStorage?.replaceCharacters(in: range, with: text)
        textView.didChangeText()
        textView.setSelectedRange(NSRange(location: range.location + (text as NSString).length, length: 0))
    }
}
