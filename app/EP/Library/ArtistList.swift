import EPAPI
import Foundation
import Observation

/// The artists column (§3.2): artists by sort name, filtered on the server as you type,
/// with or without the radio-only ones, loaded a page at a time.
///
/// `GET /artists` pages by offset and reports the whole list's length (`X-Total-Count`),
/// so the list has `total` rows from the first answer on and loads the page of a row
/// when the row appears. Type-to-select and `reveal(_:)` (navigate-to) ask the server
/// where a row is (`GET /artists/position`, same filter) and load just that page. A new
/// filter or toggle starts again from the first page. Every load carries a generation,
/// so an answer to a stale query never lands.
@Observable
final class ArtistList {
    enum Phase: Equatable {
        case idle, loading, loaded
        case failed(String)
    }

    /// Rows in the list with the current filter, loaded or not.
    private(set) var total = 0
    /// Loaded pages by number: page `n` holds rows `n * pageSize ..< (n + 1) * pageSize`.
    private(set) var pages: [Int: [Components.Schemas.ArtistRead]] = [:]
    private(set) var phase = Phase.idle
    /// Changes when a new list (filter, toggle) has arrived: the view scrolls to the
    /// selection again.
    private(set) var listVersion = 0
    /// The filter as typed (the field's text); the query uses it after `debounce`.
    private(set) var filter = ""
    private(set) var includeRadioOnly = false

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored let pageSize: Int
    @ObservationIgnored let debounce: Duration
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var pageLoads: [Int: Task<Void, Never>] = [:]
    /// The latest load started (tests await it).
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

    /// The artist in row `index`, if its page is loaded.
    func artist(at index: Int) -> Components.Schemas.ArtistRead? {
        guard index >= 0, let page = pages[index / pageSize] else { return nil }
        let offset = index % pageSize
        return offset < page.count ? page[offset] : nil
    }

    /// The row of a loaded artist.
    func index(of id: String) -> Int? {
        for (number, page) in pages {
            if let offset = page.firstIndex(where: { $0.id == id }) { return number * pageSize + offset }
        }
        return nil
    }

    // MARK: - Filter, toggle, reload

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

    /// Loads the loaded pages again (window activation, reconnect). Rows stay visible
    /// until the answer replaces them.
    func reload() {
        start(debounced: false, keepingPages: true)
    }

    private func start(debounced: Bool, keepingPages: Bool = false) {
        pendingLoad?.cancel()
        pageLoads.values.forEach { $0.cancel() }
        pageLoads = [:]
        generation += 1
        let current = generation
        let numbers = keepingPages ? Set(pages.keys).union([0]).sorted() : [0]
        let (query, radio) = (query, includeRadioOnly)
        phase = .loading
        pendingLoad = Task { [debounce] in
            if debounced {
                do { try await Task.sleep(for: debounce) } catch { return }
            }
            do {
                var loaded: [Int: [Components.Schemas.ArtistRead]] = [:]
                var total = 0
                for number in numbers where number == 0 || number * pageSize < total {
                    let (page, count) = try await fetch(page: number, query: query, radio: radio)
                    loaded[number] = page
                    total = count
                }
                guard current == generation, !Task.isCancelled else { return }
                pages = loaded
                self.total = total
                phase = .loaded
                if !keepingPages { listVersion += 1 }
            } catch {
                guard current == generation, !ServerConnection.isCancellation(error) else { return }
                phase = .failed(String(localized: "Couldn’t load artists."))
                connection.reportFailure(error)
            }
        }
    }

    // MARK: - Pages

    /// A row whose artist is not loaded appeared: load its page.
    func loadPage(containing index: Int) {
        guard index >= 0, index < total else { return }
        pendingLoad = Task { await load(page: index / pageSize) }
    }

    /// Loads page `number` unless it is loaded or loading (then waits for that load).
    private func load(page number: Int) async {
        if pages[number] != nil { return }
        if let running = pageLoads[number] { return await running.value }
        let current = generation
        let (query, radio) = (query, includeRadioOnly)
        let task = Task {
            do {
                let (page, total) = try await fetch(page: number, query: query, radio: radio)
                guard current == generation, !Task.isCancelled else { return }
                pages[number] = page
                self.total = total
            } catch {
                guard current == generation, !ServerConnection.isCancellation(error) else { return }
                connection.reportFailure(error)
            }
            if current == generation { pageLoads[number] = nil }
        }
        pageLoads[number] = task
        await task.value
    }

    private func fetch(page number: Int, query: String?, radio: Bool) async throws -> (
        [Components.Schemas.ArtistRead], Int
    ) {
        let ok = try await connection.client.listArtists(
            query: .init(includeRadioOnly: radio, q: query, limit: pageSize, offset: number * pageSize)
        ).ok
        return (try ok.body.json, ok.headers.xTotalCount)
    }

    // MARK: - Type-to-select and navigate-to

    /// Type-to-select: the first artist that sorts at or after `text` (as the server
    /// orders the list: "bjo" → Björk), with its page loaded; nil past the end.
    func artistID(atOrAfter text: String) async -> String? {
        guard !text.isEmpty else { return nil }
        guard let position = await position(id: nil, sortName: text), let id = position.id else { return nil }
        await load(page: position.offset / pageSize)
        return id
    }

    /// Navigate-to: the row of artist `id`, loading its page if needed; nil if the list
    /// (with its filter) leaves the artist out.
    func reveal(_ id: String) async -> Int? {
        if let index = index(of: id) { return index }
        guard let position = await position(id: id, sortName: nil) else { return nil }
        await load(page: position.offset / pageSize)
        return index(of: id)
    }

    private func position(id: String?, sortName: String?) async -> Components.Schemas.ArtistPositionRead? {
        let current = generation
        do {
            let output = try await connection.client.getArtistPosition(
                query: .init(includeRadioOnly: includeRadioOnly, q: query, id: id, sortName: sortName)
            )
            // Not found: the filter leaves the artist out.
            guard case .ok(let ok) = output, current == generation else { return nil }
            return try ok.body.json
        } catch {
            if !ServerConnection.isCancellation(error) { connection.reportFailure(error) }
            return nil
        }
    }
}
