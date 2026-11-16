import EPAPI
import Foundation

/// A link in a note body as stored: `[[type:uuid|Display text]]` (server: `ep.notes.links`).
/// Every range is in UTF-16 code units into the body (`NSString`/`NSRange`), as the API's
/// `TextRange` counts them.
nonisolated struct NoteLink: Equatable, Sendable {
    var type: Components.Schemas.EntityType
    /// The UUID as written (lowercased): the entity's MBID if it has one, else its ID.
    var ref: String
    /// Unescaped display text; nil when the link has none (`[[artist:uuid]]`).
    var display: String?
    /// The whole `[[…]]`.
    var range: NSRange
    /// `type:uuid|` (or `type:uuid` without display text): never shown.
    var hiddenRange: NSRange
    /// The display text as written (escaped); empty at the end of `hiddenRange` without one.
    var displayRange: NSRange

    var openRange: NSRange { NSRange(location: range.location, length: 2) }
    var closeRange: NSRange { NSRange(location: NSMaxRange(range) - 2, length: 2) }
    /// The key the document keeps link targets under.
    var key: LinkKey { LinkKey(type: type, ref: ref) }
}

/// A link target as written: its type and UUID (lowercased).
nonisolated struct LinkKey: Hashable, Sendable {
    var type: Components.Schemas.EntityType
    var ref: String

    init(type: Components.Schemas.EntityType, ref: String) {
        self.type = type
        self.ref = ref.lowercased()
    }
}

nonisolated enum NoteLinks {
    private static let types = Components.Schemas.EntityType.allCases.map(\.rawValue).joined(separator: "|")
    private static let uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    // swift-format-ignore
    private static let link = try! NSRegularExpression(
        pattern: #"\[\[("# + types + "):(" + uuid + #")(?:\|((?:\\.|[^\\\]\n])*))?\]\]"#)
    private static let unescapePattern = try! NSRegularExpression(pattern: #"\\([\\|\[\]])"#)
    private static let escapePattern = try! NSRegularExpression(pattern: #"([\\|\[\]])"#)

    /// The links in a body, in order; not in code (fences, code spans) and not escaped
    /// (`\[[…]]`), as the server parses them.
    static func parse(_ body: String) -> [NoteLink] {
        parse(body as NSString, code: CodeRegions(body as NSString))
    }

    static func parse(_ text: NSString, code: CodeRegions) -> [NoteLink] {
        let whole = NSRange(location: 0, length: text.length)
        return link.matches(in: text as String, range: whole).compactMap { match in
            let range = match.range
            guard !isEscaped(text, at: range.location), !code.contains(range),
                let type = Components.Schemas.EntityType(rawValue: text.substring(with: match.range(at: 1)))
            else { return nil }
            let displayRange = match.range(at: 3)
            let hasDisplay = displayRange.location != NSNotFound
            let hiddenStart = range.location + 2
            let hiddenEnd = hasDisplay ? displayRange.location : NSMaxRange(range) - 2
            let display = hasDisplay ? unescape(text.substring(with: displayRange)) : nil
            return NoteLink(
                type: type,
                ref: text.substring(with: match.range(at: 2)).lowercased(),
                display: display?.isEmpty == true ? nil : display,
                range: range,
                hiddenRange: NSRange(location: hiddenStart, length: hiddenEnd - hiddenStart),
                displayRange: hasDisplay ? displayRange : NSRange(location: hiddenEnd, length: 0)
            )
        }
    }

    /// The markup for a link (`ep.notes.links.format_link`).
    static func format(type: Components.Schemas.EntityType, ref: String, display: String?) -> String {
        let text = display.flatMap { $0.isEmpty ? nil : "|" + escape($0) } ?? ""
        return "[[\(type.rawValue):\(ref)\(text)]]"
    }

    /// Display text as written in a link: `|`, `[`, `]` and `\` escaped, line breaks as spaces.
    static func escape(_ display: String) -> String {
        let oneLine = display.split(whereSeparator: \.isNewline).joined(separator: " ")
        let range = NSRange(location: 0, length: (oneLine as NSString).length)
        return escapePattern.stringByReplacingMatches(in: oneLine, range: range, withTemplate: #"\\$1"#)
    }

    static func unescape(_ written: String) -> String {
        let range = NSRange(location: 0, length: (written as NSString).length)
        return unescapePattern.stringByReplacingMatches(in: written, range: range, withTemplate: "$1")
    }

    /// The character at `index` follows an odd number of backslashes.
    static func isEscaped(_ text: NSString, at index: Int) -> Bool {
        var count = 0
        while index - count > 0, text.character(at: index - count - 1) == backslash { count += 1 }
        return count % 2 == 1
    }

    private static let backslash = unichar(UInt8(ascii: "\\"))
}

/// Where a body has code, in which nothing is a link or Markdown (CommonMark rules as the
/// server's parser applies them): fenced blocks (``` or ~~~, up to three spaces indented,
/// closed by a fence of the same character at least as long, or open to the end) and
/// inline code spans within a paragraph.
nonisolated struct CodeRegions: Equatable {
    /// Whole fenced blocks, fence lines included.
    var blocks: [NSRange] = []
    /// Inline code spans, backticks included.
    var spans: [NSRange] = []

    private static let fence = try! NSRegularExpression(pattern: #"^ {0,3}(`{3,}|~{3,})(.*)$"#)
    private static let backticks = try! NSRegularExpression(pattern: "`+")

    init(_ text: NSString) {
        var paragraph: [NSRange] = []
        var open: (char: unichar, length: Int, start: Int)?
        func endParagraph() {
            if let first = paragraph.first, let last = paragraph.last {
                let range = NSRange(location: first.location, length: NSMaxRange(last) - first.location)
                spans += Self.codeSpans(text, in: range)
            }
            paragraph = []
        }
        for line in Lines(text) {
            let content = text.substring(with: line.content)
            let match = Self.fence.firstMatch(in: content, range: NSRange(location: 0, length: line.content.length))
            let fenceText = match.map { (content as NSString).substring(with: $0.range(at: 1)) as NSString }
            let info = match.map { (content as NSString).substring(with: $0.range(at: 2)) }
            if let current = open {
                if let fenceText, fenceText.character(at: 0) == current.char, fenceText.length >= current.length,
                    info?.trimmingCharacters(in: .init(charactersIn: " \t")).isEmpty == true
                {
                    blocks.append(NSRange(location: current.start, length: NSMaxRange(line.whole) - current.start))
                    open = nil
                }
                continue
            }
            if let fenceText, let info, !(fenceText.character(at: 0) == Self.backtick && info.contains("`")) {
                endParagraph()
                open = (fenceText.character(at: 0), fenceText.length, line.whole.location)
            } else if content.trimmingCharacters(in: .init(charactersIn: " \t")).isEmpty {
                endParagraph()
            } else {
                paragraph.append(line.content)
            }
        }
        if let open { blocks.append(NSRange(location: open.start, length: text.length - open.start)) }
        endParagraph()
    }

    /// `range` overlaps code.
    func contains(_ range: NSRange) -> Bool {
        (blocks + spans).contains { NSIntersectionRange($0, range).length > 0 }
    }

    func isInBlock(_ location: Int) -> Bool {
        blocks.contains { NSLocationInRange(location, $0) }
    }

    private static let backtick = unichar(UInt8(ascii: "`"))

    /// A backtick run opens a span closed by the next run of the same length; an opener
    /// without one is literal text. Outside code, "\`" is a literal backtick.
    private static func codeSpans(_ text: NSString, in range: NSRange) -> [NSRange] {
        let runs = backticks.matches(in: text as String, range: range).map(\.range)
        var spans: [NSRange] = []
        var i = 0
        while i < runs.count {
            var start = runs[i].location
            let end = NSMaxRange(runs[i])
            if NoteLinks.isEscaped(text, at: start) { start += 1 }
            let length = end - start
            guard length > 0, let close = runs[(i + 1)...].firstIndex(where: { $0.length == length }) else {
                i += 1
                continue
            }
            spans.append(NSRange(location: start, length: NSMaxRange(runs[close]) - start))
            i = close + 1
        }
        return spans
    }
}

/// The lines of a text in UTF-16 ranges; line breaks are \n, \r\n or \r (CommonMark).
nonisolated struct Lines: Sequence {
    struct Line {
        /// The line with its line break.
        var whole: NSRange
        /// Without the line break.
        var content: NSRange
    }

    let text: NSString
    init(_ text: NSString) { self.text = text }

    func makeIterator() -> AnyIterator<Line> {
        var position = 0
        let length = text.length
        let cr = unichar(13), lf = unichar(10)
        return AnyIterator {
            guard position < length else { return nil }
            let start = position
            var end = start
            while end < length, text.character(at: end) != cr, text.character(at: end) != lf { end += 1 }
            var next = end
            if next < length {
                next += (text.character(at: next) == cr && next + 1 < length && text.character(at: next + 1) == lf) ? 2 : 1
            }
            position = next
            return Line(
                whole: NSRange(location: start, length: next - start),
                content: NSRange(location: start, length: end - start))
        }
    }
}
