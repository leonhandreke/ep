import EPAPI
import Foundation
import Observation

/// An unfinished link being typed: `[[` and the text after it up to the caret, on one line,
/// outside code and outside a finished link.
nonisolated struct LinkQuery: Equatable {
    /// From `[[` to the caret, plus a `]]` right after the caret: what a chosen link replaces.
    var range: NSRange
    /// What was typed after `[[`.
    var text: String

    static let maximumLength = 100

    static func find(in text: NSString, caret: Int, styling: NoteStyling) -> LinkQuery? {
        guard caret <= text.length, !styling.code.contains(NSRange(location: max(caret - 1, 0), length: caret > 0 ? 1 : 0)),
            !styling.links.contains(where: { $0.range.location < caret && caret < NSMaxRange($0.range) })
        else { return nil }
        let open = unichar(UInt8(ascii: "[")), close = unichar(UInt8(ascii: "]"))
        var index = caret - 1
        while index >= 1, caret - index <= maximumLength + 2 {
            let char = text.character(at: index)
            if char == 10 || char == 13 { return nil }
            if char == close, text.character(at: index - 1) == close { return nil }
            if char == open, text.character(at: index - 1) == open {
                let start = index - 1
                guard !NoteLinks.isEscaped(text, at: start) else { return nil }
                var end = caret
                if caret + 1 < text.length, text.character(at: caret) == close, text.character(at: caret + 1) == close {
                    end += 2
                }
                let typed = text.substring(with: NSRange(location: index + 1, length: caret - index - 1))
                return LinkQuery(range: NSRange(location: start, length: end - start), text: typed)
            }
            index -= 1
        }
        return nil
    }
}

/// The `[[` link suggestions (board 2): library search results for what is typed, or the
/// no-match actions. The text view feeds it the query and its keys; a choice comes back
/// through `onChoose`.
@Observable
final class LinkAutocomplete {
    enum Row: Equatable {
        case hit(EntitySummary)
        case newArtist(String)
        case newOther(String)
        case searchMusicBrainz(String)
    }

    /// How a choice was made: ↩, ⇥ (insert and keep typing), ⌥↩, ⇧↩, or a click.
    enum Key {
        case accept, acceptAndContinue, alternate, shifted
    }

    enum Choice: Equatable {
        /// Insert this link markup in place of the query (then a space when continuing).
        case insert(EntitySummary, continueTyping: Bool)
        case newArtist(String)
        case newOther(String)
        case searchMusicBrainz(String)
    }

    private(set) var query: LinkQuery?
    private(set) var hits: [EntitySummary] = []
    private(set) var isSearching = false
    private(set) var selection = 0
    /// The query whose results `hits` are.
    private(set) var resultsQuery: String?

    var isOpen: Bool { query != nil }

    var rows: [Row] {
        guard let query else { return [] }
        let text = query.text.trimmingCharacters(in: .whitespaces)
        if !hits.isEmpty { return hits.map(Row.hit) }
        guard !text.isEmpty, resultsQuery == query.text, !isSearching else { return [] }
        return [.newArtist(text), .newOther(text), .searchMusicBrainz(text)]
    }

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var searchTask: Task<Void, Never>?
    /// Escape closed the suggestions for the `[[` at this location.
    @ObservationIgnored private var dismissedAt: Int?

    static let limit = 8

    init(connection: ServerConnection, debounce: Duration = .milliseconds(120)) {
        self.connection = connection
        self.debounce = debounce
    }

    /// The text view's current query (nil: no unfinished link at the caret).
    func update(_ query: LinkQuery?) {
        guard let query, query.range.location != dismissedAt else {
            if query == nil { dismissedAt = nil }
            close()
            return
        }
        let changed = query.text != self.query?.text
        self.query = query
        guard changed else { return }
        selection = 0
        search(query.text)
    }

    func close() {
        searchTask?.cancel()
        query = nil
        hits = []
        resultsQuery = nil
        isSearching = false
        selection = 0
    }

    /// Escape: closed until the caret leaves this `[[`.
    func dismiss() {
        dismissedAt = query?.range.location
        close()
    }

    func move(_ offset: Int) {
        let count = rows.count
        guard count > 0 else { return }
        selection = min(max(selection + offset, 0), count - 1)
    }

    func select(_ index: Int) {
        guard rows.indices.contains(index) else { return }
        selection = index
    }

    /// What a key does on the suggestions; nil when there is nothing to choose (the key
    /// then does what it does in the text). With no match, ↩ is a new artist, ⌥↩ a new
    /// album or recording, ⇧↩ a MusicBrainz search, whichever row is highlighted.
    func choice(for key: Key) -> Choice? {
        let rows = rows
        guard rows.indices.contains(selection) else { return nil }
        switch rows[selection] {
        case .hit(let hit):
            guard key == .accept || key == .acceptAndContinue else { return nil }
            return .insert(hit, continueTyping: key == .acceptAndContinue)
        case .newArtist(let name), .newOther(let name), .searchMusicBrainz(let name):
            switch key {
            case .alternate: return .newOther(name)
            case .shifted: return .searchMusicBrainz(name)
            case .accept, .acceptAndContinue:
                switch rows[selection] {
                case .newOther: return .newOther(name)
                case .searchMusicBrainz: return .searchMusicBrainz(name)
                default: return .newArtist(name)
                }
            }
        }
    }

    /// Waits for the search in flight (tests).
    func settle() async { await searchTask?.value }

    private func search(_ text: String) {
        searchTask?.cancel()
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else {
            hits = []
            resultsQuery = text
            isSearching = false
            return
        }
        isSearching = true
        let client = connection.client
        let debounce = debounce
        searchTask = Task {
            do {
                if debounce > .zero { try await Task.sleep(for: debounce) }
                let found = try await client.searchLibrary(
                    query: .init(q: trimmed, limit: Self.limit, includeRadioOnly: true)
                ).ok.body.json
                guard !Task.isCancelled, query?.text == text else { return }
                hits = found.map(EntitySummary.init)
                resultsQuery = text
                selection = 0
                isSearching = false
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                hits = []
                resultsQuery = text
                isSearching = false
                connection.reportFailure(error)
            }
        }
    }
}
