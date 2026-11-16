import AppKit

/// The note editor's text view (TextKit 2): the body as stored, styled as live-preview
/// Markdown (`NoteStyling`) through attributes only, so copying, pasting and undo work on
/// the stored text, links included (`isRichText` is off: the pasteboard gets plain text).
///
/// Holding ⌘ underlines every link; the one under the pointer gets a soft background and
/// a pointing hand, and ⌘-click (⌥⌘-click: new window) goes to `onOpenLink`.
final class NoteTextView: NSTextView {
    /// The styling of the text as it is now (links, code, spans).
    private(set) var styling = NoteStyling("", selection: NSRange())
    /// How a link looks: resolved (with its target), broken, or not known yet.
    var linkState: (NoteLink) -> LinkState = { _ in .unknown }
    var onOpenLink: ((_ index: Int, _ newWindow: Bool, _ event: NSEvent) -> Void)?
    var onOpenWebLink: ((URL) -> Void)?
    var placeholder = ""
    /// The width changed: the height follows.
    var onResize: (() -> Void)?

    enum LinkState: Equatable {
        case resolved(LinkTarget)
        case broken
        case unknown
    }

    private var commandHeld = false
    private var hoveredLink: Int?
    nonisolated(unsafe) private var flagsMonitor: Any?
    private let ownUndoManager = UndoManager()

    static func make() -> NoteTextView {
        let view = NoteTextView(usingTextLayoutManager: true)
        view.isRichText = false
        view.importsGraphics = false
        view.allowsUndo = true
        view.drawsBackground = false
        view.isAutomaticQuoteSubstitutionEnabled = false
        view.isAutomaticDashSubstitutionEnabled = false
        view.isAutomaticTextReplacementEnabled = false
        view.isAutomaticLinkDetectionEnabled = false
        view.smartInsertDeleteEnabled = false
        view.textContainerInset = .zero
        view.textContainer?.lineFragmentPadding = 0
        view.textContainer?.widthTracksTextView = true
        view.isVerticallyResizable = false
        view.isHorizontallyResizable = false
        view.insertionPointColor = NSPalette.accent
        view.typingAttributes = NoteAttributes.base
        view.textLayoutManager?.delegate = view.fragmentProvider
        return view
    }

    private let fragmentProvider = NoteLayoutFragmentProvider()

    override var undoManager: UndoManager? { ownUndoManager }

    /// Forgets what undo would bring back (the text was replaced from outside).
    func resetUndo() { ownUndoManager.removeAllActions() }

    // MARK: - Styling

    /// Styles the whole text for the current selection. Attribute changes are not edits:
    /// they do not reach undo or the document.
    func restyle() {
        guard let storage = textStorage, !hasMarkedText() else { return }
        let text = storage.string as NSString
        styling = NoteStyling(text, selection: selectedRange())
        let whole = NSRange(location: 0, length: text.length)
        storage.beginEditing()
        storage.setAttributes(NoteAttributes.base, range: whole)
        for span in styling.spans { apply(span, to: storage) }
        storage.endEditing()
        typingAttributes = NoteAttributes.base
        needsDisplay = true
    }

    private func apply(_ span: NoteStyling.Span, to storage: NSTextStorage) {
        let range = span.range
        guard range.length > 0, NSMaxRange(range) <= storage.length else { return }
        switch span.kind {
        case .heading(let level):
            storage.addAttribute(.font, value: NoteAttributes.heading(level), range: range)
            storage.addAttribute(.paragraphStyle, value: NoteAttributes.headingParagraph, range: lineRange(range, in: storage))
        case .quote:
            storage.addAttribute(.foregroundColor, value: NSPalette.text2, range: range)
            storage.addAttribute(.paragraphStyle, value: NoteAttributes.quoteParagraph, range: lineRange(range, in: storage))
            storage.addAttribute(.noteDecoration, value: NoteDecoration(.quoteBar(color: NSPalette.line)), range: range)
        case .listItem:
            storage.addAttribute(.paragraphStyle, value: NoteAttributes.listParagraph, range: lineRange(range, in: storage))
        case .codeBlock:
            storage.addAttribute(.font, value: NoteAttributes.code, range: range)
            storage.addAttribute(.foregroundColor, value: NSPalette.text2, range: range)
        case .bold, .italic:
            let trait: NSFontTraitMask = span.kind == .bold ? .boldFontMask : .italicFontMask
            storage.enumerateAttribute(.font, in: range) { value, subrange, _ in
                guard let font = value as? NSFont else { return }
                storage.addAttribute(.font, value: NSFontManager.shared.convert(font, toHaveTrait: trait), range: subrange)
            }
        case .inlineCode:
            storage.addAttribute(.font, value: NoteAttributes.code, range: range)
            storage.addAttribute(.backgroundColor, value: NSPalette.chip, range: range)
        case .link(let index):
            styleLink(index, range: range, in: storage)
        case .untitledLink(let index):
            // No display text: the hidden `type:uuid` makes room for the target's title.
            let title: String
            if case .resolved(let target) = linkState(styling.links[index]) {
                title = target.title
            } else {
                title = styling.links[index].type.kindLabel
            }
            hide(range, in: storage)
            let last = NSRange(location: NSMaxRange(range) - 1, length: 1)
            storage.addAttribute(.kern, value: NoteAttributes.width(of: title), range: last)
            storage.addAttribute(
                .noteDecoration,
                value: NoteDecoration(.title(title, font: NoteAttributes.bodyFont, color: linkColor(index))), range: last)
            styleLink(index, range: range, in: storage, decorate: false)
        case .webLink(let url):
            storage.addAttribute(.foregroundColor, value: NSPalette.accent, range: range)
            storage.addAttribute(.noteWebLink, value: url, range: range)
            storage.addAttribute(.toolTip, value: url, range: range)
            if commandHeld { storage.addAttribute(.underlineStyle, value: NSUnderlineStyle.single.rawValue, range: range) }
        case .marker:
            storage.addAttribute(.foregroundColor, value: NSPalette.text3, range: range)
        case .hidden:
            hide(range, in: storage)
        case .bullet:
            storage.addAttribute(.foregroundColor, value: NSColor.clear, range: range)
            storage.addAttribute(
                .noteDecoration, value: NoteDecoration(.bullet(font: NoteAttributes.bodyFont, color: NSPalette.text3)),
                range: range)
        }
    }

    private func styleLink(_ index: Int, range: NSRange, in storage: NSTextStorage, decorate: Bool = true) {
        let link = styling.links[index]
        let state = linkState(link)
        let whole = link.range
        storage.addAttribute(.noteLink, value: index, range: whole)
        if decorate { storage.addAttribute(.foregroundColor, value: linkColor(index), range: range) }
        switch state {
        case .broken:
            storage.addAttribute(.underlineStyle, value: NoteAttributes.dashedUnderline, range: range)
            storage.addAttribute(.underlineColor, value: NSPalette.danger, range: range)
            storage.addAttribute(.toolTip, value: String(localized: "Link target no longer exists"), range: whole)
            // The ⚠︎ icon after the link, in room made after its last character.
            let last = NSRange(location: NSMaxRange(whole) - 1, length: 1)
            storage.addAttribute(.kern, value: NoteAttributes.brokenIconWidth, range: last)
            storage.addAttribute(
                .noteDecoration,
                value: NoteDecoration(.brokenIcon(color: NSPalette.danger, width: NoteAttributes.brokenIconWidth)),
                range: last)
        case .resolved(let target):
            let tip = String(localized: "\(target.ref.type.kindLabel) · \(target.title) — ⌘-click to open · ⌥⌘ new window")
            storage.addAttribute(.toolTip, value: tip, range: whole)
        case .unknown:
            storage.addAttribute(.toolTip, value: String(localized: "⌘-click to open · ⌥⌘ new window"), range: whole)
        }
        if commandHeld {
            if state != .broken {
                storage.addAttribute(.underlineStyle, value: NSUnderlineStyle.single.rawValue, range: range)
            }
            if hoveredLink == index { storage.addAttribute(.backgroundColor, value: NSPalette.selSoft, range: range) }
        }
    }

    private func linkColor(_ index: Int) -> NSColor {
        linkState(styling.links[index]) == .broken ? NSPalette.danger : NSPalette.accent
    }

    private func hide(_ range: NSRange, in storage: NSTextStorage) {
        storage.addAttribute(.font, value: NoteAttributes.hidden, range: range)
        storage.addAttribute(.foregroundColor, value: NSColor.clear, range: range)
    }

    private func lineRange(_ range: NSRange, in storage: NSTextStorage) -> NSRange {
        (storage.string as NSString).paragraphRange(for: range)
    }

    // MARK: - ⌘: links as targets

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let flagsMonitor { NSEvent.removeMonitor(flagsMonitor) }
        flagsMonitor = nil
        guard window != nil else { return }
        flagsMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.setCommandHeld(event.modifierFlags.contains(.command))
            return event
        }
    }

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        for area in trackingAreas where area.owner === self && area.userInfo?["note"] != nil { removeTrackingArea(area) }
        addTrackingArea(
            NSTrackingArea(
                rect: .zero, options: [.mouseMoved, .mouseEnteredAndExited, .activeInActiveApp, .inVisibleRect],
                owner: self, userInfo: ["note": true]))
    }

    private func setCommandHeld(_ held: Bool) {
        guard held != commandHeld else { return }
        commandHeld = held
        hoveredLink = held ? linkIndex(atWindowPoint: window?.mouseLocationOutsideOfEventStream) : nil
        restyle()
        updateCursor()
    }

    override func mouseMoved(with event: NSEvent) {
        guard commandHeld else { return super.mouseMoved(with: event) }
        let hovered = linkIndex(atWindowPoint: event.locationInWindow)
        if hovered != hoveredLink {
            hoveredLink = hovered
            restyle()
        }
        updateCursor()
    }

    override func mouseExited(with event: NSEvent) {
        super.mouseExited(with: event)
        guard hoveredLink != nil else { return }
        hoveredLink = nil
        restyle()
    }

    private func updateCursor() {
        if commandHeld, hoveredLink != nil || webLink(atWindowPoint: window?.mouseLocationOutsideOfEventStream) != nil {
            NSCursor.pointingHand.set()
        } else {
            NSCursor.iBeam.set()
        }
    }

    override func mouseDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command) {
            if let index = linkIndex(atWindowPoint: event.locationInWindow) {
                return onOpenLink?(index, event.modifierFlags.contains(.option), event) ?? ()
            }
            if let url = webLink(atWindowPoint: event.locationInWindow) {
                return onOpenWebLink?(url) ?? ()
            }
        }
        super.mouseDown(with: event)
    }

    /// The note link under a point in window coordinates.
    func linkIndex(atWindowPoint point: NSPoint?) -> Int? {
        guard let index = characterIndex(atWindowPoint: point) else { return nil }
        return textStorage?.attribute(.noteLink, at: index, effectiveRange: nil) as? Int
    }

    private func webLink(atWindowPoint point: NSPoint?) -> URL? {
        guard let index = characterIndex(atWindowPoint: point),
            let string = textStorage?.attribute(.noteWebLink, at: index, effectiveRange: nil) as? String
        else { return nil }
        return URL(string: string)
    }

    /// The character under a point (not the insertion point nearest to it): nil past the
    /// end of a line.
    private func characterIndex(atWindowPoint point: NSPoint?) -> Int? {
        guard let point, let layout = textLayoutManager, let content = layout.textContentManager,
            let storage = textStorage, storage.length > 0
        else { return nil }
        var local = convert(point, from: nil)
        local.x -= textContainerOrigin.x
        local.y -= textContainerOrigin.y
        guard let fragment = layout.textLayoutFragment(for: local) else { return nil }
        let frame = fragment.layoutFragmentFrame
        let start = content.offset(from: content.documentRange.location, to: fragment.rangeInElement.location)
        for line in fragment.textLineFragments {
            let bounds = line.typographicBounds.offsetBy(dx: frame.minX, dy: frame.minY)
            guard local.y >= bounds.minY, local.y < bounds.maxY else { continue }
            let inLine = CGPoint(x: local.x - bounds.minX, y: local.y - bounds.minY)
            let index = line.characterIndex(for: inLine)
            guard index >= line.characterRange.location, index < NSMaxRange(line.characterRange) else { return nil }
            // The pointer must be over the character, not past the line's end.
            let end = line.locationForCharacter(at: NSMaxRange(line.characterRange)).x
            guard inLine.x <= end else { return nil }
            let documentIndex = start + index
            return documentIndex < storage.length ? documentIndex : nil
        }
        return nil
    }

    deinit {
        if let flagsMonitor { NSEvent.removeMonitor(flagsMonitor) }
    }

    override func setFrameSize(_ newSize: NSSize) {
        let widthChanged = newSize.width != frame.width
        super.setFrameSize(newSize)
        if widthChanged { onResize?() }
    }

    // MARK: - Placeholder

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard string.isEmpty, !placeholder.isEmpty else { return }
        NSAttributedString(
            string: placeholder,
            attributes: [.font: NoteAttributes.bodyFont, .foregroundColor: NSPalette.text3]
        ).draw(at: textContainerOrigin)
    }
}

// MARK: - Attributes

nonisolated extension NSAttributedString.Key {
    /// The index of the note link (in `NoteStyling.links`) a character belongs to.
    static let noteLink = NSAttributedString.Key("EPNoteLink")
    /// The URL of a Markdown link `[text](url)`.
    static let noteWebLink = NSAttributedString.Key("EPNoteWebLink")
    /// Something `NoteLayoutFragment` draws at a character (`NoteDecoration`).
    static let noteDecoration = NSAttributedString.Key("EPNoteDecoration")
}

/// What the layout fragments draw besides the text: list bullets, the quote bar, the
/// broken-link icon, the title of a link without display text. Carries its own font and
/// colours: fragments draw outside the main actor's types.
nonisolated final class NoteDecoration: NSObject, @unchecked Sendable {
    enum Kind: Equatable {
        case bullet(font: NSFont, color: NSColor)
        case quoteBar(color: NSColor)
        /// An icon in the room made after the character (`width`).
        case brokenIcon(color: NSColor, width: CGFloat)
        case title(String, font: NSFont, color: NSColor)
    }

    let kind: Kind
    init(_ kind: Kind) { self.kind = kind }

    override func isEqual(_ object: Any?) -> Bool { (object as? NoteDecoration)?.kind == kind }
    override var hash: Int {
        switch kind {
        case .bullet: 1
        case .quoteBar: 2
        case .brokenIcon: 3
        case .title(let title, _, _): title.hashValue
        }
    }
}

/// Fonts and paragraph styles of the note body (design §3.7 and board 2; decision 4).
enum NoteAttributes {
    static let bodyFont = NSFont.systemFont(ofSize: Typography.noteSizes.body)
    static let code = NSFont.monospacedSystemFont(ofSize: Typography.noteSizes.code, weight: .regular)
    /// Hidden syntax: practically no width, no colour.
    static let hidden = NSFont.systemFont(ofSize: 0.1)
    static let brokenIconWidth: CGFloat = 16
    static let dashedUnderline = NSUnderlineStyle.single.rawValue | NSUnderlineStyle.patternDash.rawValue

    static func heading(_ level: Int) -> NSFont {
        let sizes = Typography.noteSizes.headings
        return NSFont.systemFont(ofSize: sizes[min(max(level, 1), sizes.count) - 1], weight: .semibold)
    }

    static func width(of title: String) -> CGFloat {
        (title as NSString).size(withAttributes: [.font: bodyFont]).width
    }

    static let bodyParagraph: NSParagraphStyle = {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = Typography.noteLineSpacing
        style.paragraphSpacing = 8
        return style
    }()

    static let headingParagraph: NSParagraphStyle = {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = 4
        style.paragraphSpacingBefore = 4
        style.paragraphSpacing = 6
        return style
    }()

    static let quoteParagraph: NSParagraphStyle = {
        let style = bodyParagraph.mutableCopy() as! NSMutableParagraphStyle
        style.firstLineHeadIndent = 15
        style.headIndent = 15
        return style
    }()

    static let listParagraph: NSParagraphStyle = {
        let style = NSMutableParagraphStyle()
        style.lineSpacing = Typography.noteLineSpacing
        style.paragraphSpacing = 4
        style.firstLineHeadIndent = 4
        style.headIndent = 4
        return style
    }()

    static let base: [NSAttributedString.Key: Any] = [
        .font: bodyFont, .foregroundColor: NSPalette.text, .paragraphStyle: bodyParagraph,
    ]
}

// MARK: - Layout fragments

/// Hands TextKit 2 a layout fragment that also draws the decorations.
nonisolated final class NoteLayoutFragmentProvider: NSObject, NSTextLayoutManagerDelegate {
    func textLayoutManager(
        _ textLayoutManager: NSTextLayoutManager, textLayoutFragmentFor location: any NSTextLocation,
        in textElement: NSTextElement
    ) -> NSTextLayoutFragment {
        NoteLayoutFragment(textElement: textElement, range: textElement.elementRange)
    }
}

nonisolated final class NoteLayoutFragment: NSTextLayoutFragment {
    override func draw(at point: CGPoint, in context: CGContext) {
        super.draw(at: point, in: context)
        guard let paragraph = textElement as? NSTextParagraph else { return }
        let string = paragraph.attributedString
        var decorations: [(NoteDecoration.Kind, Int)] = []
        string.enumerateAttribute(.noteDecoration, in: NSRange(location: 0, length: string.length)) { value, range, _ in
            if let decoration = value as? NoteDecoration { decorations.append((decoration.kind, range.location)) }
        }
        guard !decorations.isEmpty else { return }
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: true)
        defer { NSGraphicsContext.restoreGraphicsState() }
        var drewQuoteBar = false
        for (kind, index) in decorations {
            if case .quoteBar(let color) = kind {
                guard !drewQuoteBar else { continue }
                drewQuoteBar = true
                let height = textLineFragments.last.map { $0.typographicBounds.maxY } ?? layoutFragmentFrame.height
                color.setFill()
                NSRect(x: point.x, y: point.y, width: 3, height: height).fill()
                continue
            }
            guard let line = textLineFragments.first(where: { NSLocationInRange(index, $0.characterRange) })
            else { continue }
            let bounds = line.typographicBounds
            let x = point.x + bounds.minX + line.locationForCharacter(at: index).x
            let baseline = point.y + bounds.minY + line.glyphOrigin.y
            switch kind {
            case .bullet(let font, let color):
                draw("•", at: x, baseline: baseline, font: font, color: color)
            case .brokenIcon(let color, let width):
                // At the end of the room made after the character.
                let after = line.locationForCharacter(at: index + 1).x - line.locationForCharacter(at: index).x
                let config = NSImage.SymbolConfiguration(pointSize: 11, weight: .medium)
                    .applying(.init(paletteColors: [color]))
                if let image = NSImage(systemSymbolName: "exclamationmark.circle", accessibilityDescription: nil)?
                    .withSymbolConfiguration(config)
                {
                    let size = image.size
                    let rect = NSRect(
                        x: x + after - width + 3, y: baseline - size.height + 2, width: size.width, height: size.height)
                    image.draw(in: rect, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
                }
            case .title(let title, let font, let color):
                draw(title, at: x, baseline: baseline, font: font, color: color)
            case .quoteBar:
                break
            }
        }
    }

    private func draw(_ text: String, at x: CGFloat, baseline: CGFloat, font: NSFont, color: NSColor) {
        NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: color])
            .draw(at: NSPoint(x: x, y: baseline - font.ascender))
    }
}
