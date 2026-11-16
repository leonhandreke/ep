import Foundation

/// Live-preview Markdown for the note editor (Obsidian style): what to style how, as
/// ranges into the body (UTF-16, `NSRange`). The text view turns spans into attributes;
/// the body itself is never changed, so what is stored is what was typed.
///
/// Syntax (heading and quote markers, `**`, `*`, backticks, `[[`/`]]`, `[`…`](url)`) is
/// hidden unless the selection touches its construct, then it shows dimmed. A link's
/// `type:uuid|` is always hidden: the user never sees the UUID. Fenced code shows as is.
nonisolated struct NoteStyling: Equatable {
    struct Span: Equatable {
        var kind: Kind
        var range: NSRange
    }

    enum Kind: Equatable {
        /// A heading line's text (after its marker); levels above 3 look like 3.
        case heading(level: Int)
        /// A quote line, marker included.
        case quote
        /// A list item line, marker included.
        case listItem
        /// A fenced code block, fences included.
        case codeBlock
        case bold, italic
        /// An inline code span's text (between its backticks).
        case inlineCode
        /// The visible text of `links[index]`: its display text.
        case link(index: Int)
        /// A link without display text: its hidden `type:uuid` shows the target's title.
        case untitledLink(index: Int)
        /// A Markdown link's text (`[text](url)`).
        case webLink(url: String)
        /// Syntax shown dimmed (`--text3`).
        case marker
        /// Syntax not shown.
        case hidden
        /// A list item's `-`, `*` or `+`, drawn as "•".
        case bullet
    }

    var links: [NoteLink]
    var code: CodeRegions
    /// In order: blocks, inline styles, links, then syntax (later spans win).
    var spans: [Span]

    init(_ text: NSString, selection: NSRange) {
        code = CodeRegions(text)
        links = NoteLinks.parse(text, code: code)
        var blocks: [Span] = [], inline: [Span] = [], linkSpans: [Span] = [], syntax: [Span] = []

        func touches(_ construct: NSRange) -> Bool {
            selection.location <= NSMaxRange(construct) && NSMaxRange(selection) >= construct.location
        }
        func addSyntax(_ range: NSRange, revealedBy construct: NSRange) {
            guard range.length > 0 else { return }
            syntax.append(Span(kind: touches(construct) ? .marker : .hidden, range: range))
        }

        for block in code.blocks { blocks.append(Span(kind: .codeBlock, range: block)) }

        // Block constructs, line by line, outside fenced code.
        for line in Lines(text) where !code.isInBlock(line.whole.location) && line.content.length > 0 {
            let content = text.substring(with: line.content)
            let local = NSRange(location: 0, length: line.content.length)
            func shifted(_ range: NSRange) -> NSRange {
                NSRange(location: range.location + line.content.location, length: range.length)
            }
            if let match = Self.heading.firstMatch(in: content, range: local) {
                let marker = shifted(NSRange(location: 0, length: match.range(at: 2).location))
                let level = match.range(at: 1).length
                blocks.append(
                    Span(
                        kind: .heading(level: min(level, 3)),
                        range: NSRange(location: NSMaxRange(marker), length: NSMaxRange(line.content) - NSMaxRange(marker))
                    ))
                addSyntax(marker, revealedBy: line.content)
            } else if let match = Self.quote.firstMatch(in: content, range: local) {
                blocks.append(Span(kind: .quote, range: line.content))
                addSyntax(shifted(match.range), revealedBy: line.content)
            } else if let match = Self.listItem.firstMatch(in: content, range: local) {
                blocks.append(Span(kind: .listItem, range: line.content))
                let marker = shifted(match.range(at: 2))
                if (text.substring(with: marker) as NSString).length == 1, !Self.isDigit(text.character(at: marker.location)) {
                    syntax.append(Span(kind: .bullet, range: marker))
                } else {
                    syntax.append(Span(kind: .marker, range: marker))
                }
            }
        }

        // Code spans: monospaced text, backticks as syntax.
        var taken: [NSRange] = []
        for span in code.spans {
            let ticks = Self.backtickRunLength(text, at: span.location)
            let inner = NSRange(location: span.location + ticks, length: max(span.length - 2 * ticks, 0))
            inline.append(Span(kind: .inlineCode, range: inner))
            addSyntax(NSRange(location: span.location, length: ticks), revealedBy: span)
            addSyntax(NSRange(location: NSMaxRange(inner), length: span.length - ticks - inner.length), revealedBy: span)
            taken.append(span)
        }

        // Note links: display text visible, `type:uuid|` always hidden, brackets as syntax.
        for (index, link) in links.enumerated() {
            if link.display == nil {
                linkSpans.append(Span(kind: .untitledLink(index: index), range: link.hiddenRange))
            } else {
                linkSpans.append(Span(kind: .link(index: index), range: link.displayRange))
                syntax.append(Span(kind: .hidden, range: link.hiddenRange))
            }
            addSyntax(link.openRange, revealedBy: link.range)
            addSyntax(link.closeRange, revealedBy: link.range)
            taken.append(link.range)
        }

        // Markdown links [text](url), outside code and note links.
        let whole = NSRange(location: 0, length: text.length)
        for match in Self.webLink.matches(in: text as String, range: whole) {
            let range = match.range
            guard !Self.overlaps(range, taken), !code.isInBlock(range.location),
                !NoteLinks.isEscaped(text, at: range.location)
            else { continue }
            let label = match.range(at: 1)
            linkSpans.append(Span(kind: .webLink(url: text.substring(with: match.range(at: 2))), range: label))
            addSyntax(NSRange(location: range.location, length: 1), revealedBy: range)
            addSyntax(NSRange(location: NSMaxRange(label), length: NSMaxRange(range) - NSMaxRange(label)), revealedBy: range)
            taken.append(range)
        }

        // Emphasis: may contain links and code, but not start or end inside them.
        for (pattern, kind, marker) in [(Self.bold, Kind.bold, 2), (Self.italicStar, .italic, 1), (Self.italicUnderscore, .italic, 1)] {
            for match in pattern.matches(in: text as String, range: whole) {
                let range = match.range
                guard !code.isInBlock(range.location), !NoteLinks.isEscaped(text, at: range.location),
                    !taken.contains(where: { Self.cuts($0, range) })
                else { continue }
                let inner = NSRange(location: range.location + marker, length: range.length - 2 * marker)
                inline.append(Span(kind: kind, range: inner))
                addSyntax(NSRange(location: range.location, length: marker), revealedBy: range)
                addSyntax(NSRange(location: NSMaxRange(inner), length: marker), revealedBy: range)
            }
        }

        spans = blocks + inline + linkSpans + syntax
    }

    /// The index of the note link at a location (`range` inclusive of its start, exclusive of its end).
    func linkIndex(at location: Int) -> Int? {
        links.firstIndex { NSLocationInRange(location, $0.range) }
    }

    /// Where the caret may go: never inside a link's hidden `type:uuid|` or right after its
    /// `[[` (typing there would break the link). Moving left out of the display text
    /// lands between the brackets' characters, moving right lands at the display text.
    func adjustedCaret(_ location: Int, from previous: Int) -> Int {
        for link in links where link.display != nil {
            let hidden = link.hiddenRange
            guard location >= hidden.location, location < NSMaxRange(hidden) else { continue }
            return previous >= NSMaxRange(hidden) && location < previous ? hidden.location - 1 : NSMaxRange(hidden)
        }
        return location
    }

    // MARK: - Patterns

    private static let heading = try! NSRegularExpression(pattern: #"^ {0,3}(#{1,6})(?:[ \t]+|$)()"#)
    private static let quote = try! NSRegularExpression(pattern: #"^ {0,3}> ?"#)
    private static let listItem = try! NSRegularExpression(pattern: #"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+"#)
    private static let webLink = try! NSRegularExpression(pattern: #"(?<!\[)\[([^\[\]\n]+)\]\(([^()\s]+)\)"#)
    private static let bold = try! NSRegularExpression(pattern: #"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"#)
    private static let italicStar = try! NSRegularExpression(pattern: #"(?<![*\\])\*(?=[^\s*])(.+?)(?<=[^\s*\\])\*(?!\*)"#)
    private static let italicUnderscore = try! NSRegularExpression(pattern: #"(?<![\w\\])_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)"#)

    private static func isDigit(_ char: unichar) -> Bool { char >= 48 && char <= 57 }

    private static func backtickRunLength(_ text: NSString, at location: Int) -> Int {
        var length = 0
        while location + length < text.length, text.character(at: location + length) == 96 { length += 1 }
        return length
    }

    private static func overlaps(_ range: NSRange, _ others: [NSRange]) -> Bool {
        others.contains { NSIntersectionRange($0, range).length > 0 }
    }

    /// `other` and `range` overlap without one containing the other.
    private static func cuts(_ other: NSRange, _ range: NSRange) -> Bool {
        guard NSIntersectionRange(other, range).length > 0 else { return false }
        let contains = other.location >= range.location && NSMaxRange(other) <= NSMaxRange(range)
        return !contains
    }
}
