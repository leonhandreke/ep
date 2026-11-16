import EPAPI
import Foundation
import Observation

/// The artists column (§3.2): artists by sort name, filtered on the server as you type,
/// with or without the radio-only ones, loaded a page at a time as the list scrolls.
///
/// `GET /artists` pages by offset and gives no total, so the list holds the pages loaded
/// so far and asks for the next one when its last row appears; a short page is the end.
/// A new filter or toggle starts again from the first page. Every load carries a
/// generation, so an answer to a stale query never lands.
@Observable
final class ArtistList {
    enum Phase: Equatable {
        case idle, loading, loaded
        case failed(String)
    }

    private(set) var artists: [Components.Schemas.ArtistRead] = []
    private(set) var phase = Phase.idle
    private(set) var canLoadMore = false
    /// The filter as typed (the field's text); the query uses it after `debounce`.
    private(set) var filter = ""
    private(set) var includeRadioOnly = false

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored let pageSize: Int
    @ObservationIgnored let debounce: Duration
    @ObservationIgnored private var generation = 0
    /// The running load (tests await it).
    @ObservationIgnored private(set) var pendingLoad: Task<Void, Never>?

    init(connection: ServerConnection, pageSize: Int = 200, debounce: Duration = .milliseconds(250)) {
        self.connection = connection
        self.pageSize = pageSize
        self.debounce = debounce
    }

    /// The query the server sees: trimmed, nil when empty.
    var query: String? {
        let trimmed = filter.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Typing in the filter field: the query runs once typing pauses; an earlier one
    /// still waiting or running is cancelled.
    func setFilter(_ text: String) {
        guard text != filter else { return }
        let before = query
        filter = text
        guard query != before else { return }
        start(debounced: true)
    }

    func setIncludeRadioOnly(_ include: Bool) {
        guard include != includeRadioOnly else { return }
        includeRadioOnly = include
        start(debounced: false)
    }

    /// Loads the first page again (window activation, reconnect). Rows stay visible
    /// until the answer replaces them.
    func reload() {
        start(debounced: false, keepingCount: true)
    }

    /// Called when the last loaded row appears.
    func loadMoreIfNeeded(after id: String) {
        guard canLoadMore, phase != .loading, artists.last?.id == id else { return }
        let current = generation
        let offset = artists.count
        let (query, radio) = (query, includeRadioOnly)
        phase = .loading
        pendingLoad = Task {
            await fetch(offset: offset, limit: pageSize, query: query, radio: radio, generation: current, append: true)
        }
    }

    private func start(debounced: Bool, keepingCount: Bool = false) {
        pendingLoad?.cancel()
        generation += 1
        let current = generation
        let (query, radio) = (query, includeRadioOnly)
        // A reload asks for as many rows as are loaded (up to the API's page limit), so the
        // list does not shrink under the user.
        let limit = keepingCount ? min(max(artists.count, pageSize), 1000) : pageSize
        phase = .loading
        pendingLoad = Task { [debounce] in
            if debounced {
                do { try await Task.sleep(for: debounce) } catch { return }
            }
            await fetch(offset: 0, limit: limit, query: query, radio: radio, generation: current, append: false)
        }
    }

    private func fetch(offset: Int, limit: Int, query: String?, radio: Bool, generation current: Int, append: Bool)
        async
    {
        do {
            let page = try await connection.client.listArtists(
                query: .init(limit: limit, offset: offset, includeRadioOnly: radio, q: query)
            ).ok.body.json
            guard current == generation, !Task.isCancelled else { return }
            artists = append ? artists + page : page
            canLoadMore = page.count == limit
            phase = .loaded
        } catch {
            guard current == generation, !ServerConnection.isCancellation(error) else { return }
            phase = .failed(String(localized: "Couldn’t load artists."))
            connection.reportFailure(error)
        }
    }

    // MARK: - Type-to-select

    /// The first loaded artist whose sort name or name starts with `prefix`, ignoring
    /// case, accents and punctuation ("bjo" → Björk, "glenn" → "Glenn-Copeland, Beverly").
    func firstArtist(matching prefix: String) -> Components.Schemas.ArtistRead? {
        let needle = Self.fold(prefix)
        guard !needle.isEmpty else { return nil }
        return artists.first { Self.fold($0.sortName).hasPrefix(needle) || Self.fold($0.name).hasPrefix(needle) }
    }

    nonisolated static func fold(_ text: String) -> String {
        text.folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: nil)
            .unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) }
            .map(String.init).joined()
    }
}
