import EPAPI
import Foundation
import Observation

/// What the search palette (§8) shows for what is typed: library hits from `GET /search`
/// (typo-tolerant on the server), grouped as TOP HIT, TRACKS, ALBUMS, ARTISTS, RADIO,
/// optionally only entities with one tag (decision 7: a tag click). With a tag and
/// nothing typed, everything with the tag (the same call without `q`). Answers to an
/// older query are dropped. The re-point sheet uses it ungrouped, for recordings only.
@Observable
final class SearchStore {
    enum GroupKind: String, CaseIterable {
        case topHit, tracks, albums, artists, radio

        var title: String {
            switch self {
            case .topHit: String(localized: "Top hit")
            case .tracks: String(localized: "Tracks")
            case .albums: String(localized: "Albums")
            case .artists: String(localized: "Artists")
            case .radio: String(localized: "Radio")
            }
        }

        init(_ type: Components.Schemas.EntityType) {
            switch type {
            case .recording: self = .tracks
            case .releaseGroup: self = .albums
            case .artist: self = .artists
            case .radioShow, .radioEpisode: self = .radio
            }
        }
    }

    struct Group: Identifiable, Equatable {
        var kind: GroupKind
        var hits: [EntitySummary]
        var id: GroupKind { kind }
    }

    private(set) var query = ""
    private(set) var tag: Components.Schemas.TagRead?
    /// The hits in the order shown (group by group); `selection` indexes into it.
    private(set) var hits: [EntitySummary] = []
    private(set) var groups: [Group] = []
    private(set) var isSearching = false
    private(set) var failed = false
    private(set) var selection = 0

    var selected: EntitySummary? { hits.indices.contains(selection) ? hits[selection] : nil }
    /// Whether there is anything to search for.
    var isEmpty: Bool { trimmed.isEmpty && tag == nil }

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let types: [Components.Schemas.EntityType]?
    @ObservationIgnored private let grouped: Bool
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var searchTask: Task<Void, Never>?
    /// Counts searches: an answer to an earlier one never replaces a later one's.
    @ObservationIgnored private var generation = 0

    static let limit = 30
    static let perType = 6

    init(
        connection: ServerConnection, types: [Components.Schemas.EntityType]? = nil, grouped: Bool = true,
        debounce: Duration = .milliseconds(120)
    ) {
        self.connection = connection
        self.types = types
        self.grouped = grouped
        self.debounce = debounce
    }

    private var trimmed: String { query.trimmingCharacters(in: .whitespacesAndNewlines) }

    func update(_ query: String) {
        guard query != self.query else { return }
        self.query = query
        search()
    }

    /// Keeps only entities with `tag` (nil: all).
    func filter(by tag: Components.Schemas.TagRead?) {
        guard tag?.id != self.tag?.id else { return }
        self.tag = tag
        search()
    }

    func move(_ offset: Int) {
        guard !hits.isEmpty else { return }
        selection = min(max(selection + offset, 0), hits.count - 1)
    }

    func select(_ hit: EntitySummary) {
        if let index = hits.firstIndex(of: hit) { selection = index }
    }

    /// Waits for the search in flight (tests).
    func settle() async { await searchTask?.value }

    private func search() {
        searchTask?.cancel()
        generation += 1
        let current = generation
        let text = trimmed
        let tag = tag
        guard !text.isEmpty || tag != nil else {
            show([], query: false)
            isSearching = false
            return
        }
        isSearching = true
        failed = false
        let client = connection.client
        let (types, debounce, grouped) = (types, debounce, grouped)
        searchTask = Task {
            do {
                if debounce > .zero { try await Task.sleep(for: debounce) }
                let found = try await client.searchLibrary(
                    query: .init(
                        q: text.isEmpty ? nil : text, types: types, limit: Self.limit,
                        perType: grouped ? Self.perType : nil, tagId: tag?.id, includeRadioOnly: true)
                ).ok.body.json.map(EntitySummary.init)
                guard !Task.isCancelled, current == generation else { return }
                show(found, query: !text.isEmpty)
                isSearching = false
            } catch {
                guard !Task.isCancelled, current == generation, !ServerConnection.isCancellation(error) else { return }
                show([], query: true)
                isSearching = false
                failed = true
                connection.reportFailure(error)
            }
        }
    }

    /// Groups the hits; the best hit of a query is the top hit (a tag's list has none).
    private func show(_ found: [EntitySummary], query: Bool) {
        if grouped {
            var rest = found
            var groups: [Group] = []
            if query, !rest.isEmpty { groups.append(Group(kind: .topHit, hits: [rest.removeFirst()])) }
            for kind in GroupKind.allCases where kind != .topHit {
                let hits = rest.filter { GroupKind($0.ref.type) == kind }
                if !hits.isEmpty { groups.append(Group(kind: kind, hits: hits)) }
            }
            self.groups = groups
            hits = groups.flatMap(\.hits)
        } else {
            groups = found.isEmpty ? [] : [Group(kind: .topHit, hits: found)]
            hits = found
        }
        selection = 0
    }
}
