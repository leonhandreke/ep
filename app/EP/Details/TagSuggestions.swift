import EPAPI
import Foundation
import Observation

/// The "+ Add tag" field's suggestions (§3.7): existing tags starting with what is typed,
/// most used first (`GET /tags?prefix=&sort=count`, with their counts), without the ones the entity has, then
/// "Create “#…”" unless a tag has that name. ↑↓ move, Return adds the highlighted row.
@Observable
final class TagSuggestions {
    enum Row: Equatable {
        case existing(Components.Schemas.TagRead)
        case create(String)

        var name: String {
            switch self {
            case .existing(let tag): tag.name
            case .create(let name): name
            }
        }
    }

    private(set) var text = ""
    private(set) var tags: [Components.Schemas.TagRead] = []
    private(set) var selection = 0
    /// The prefix `tags` were found for.
    private(set) var resultsText: String?

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var task: Task<Void, Never>?
    /// Names the entity already has (left out).
    @ObservationIgnored private var excluded: Set<String> = []

    static let limit = 8

    init(connection: ServerConnection, debounce: Duration = .milliseconds(100)) {
        self.connection = connection
        self.debounce = debounce
    }

    var trimmed: String {
        text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    var rows: [Row] {
        let name = trimmed
        guard !name.isEmpty else { return [] }
        let existing = tags.filter { !excluded.contains($0.name.lowercased()) }
        var rows = existing.map(Row.existing)
        let taken = tags.contains { $0.name.lowercased() == name.lowercased() }
        if !taken, resultsText == text { rows.append(.create(name)) }
        return rows
    }

    var isOpen: Bool { !rows.isEmpty }

    /// The field's text changed; `existing` are the entity's tags.
    func update(_ text: String, existing: [Components.Schemas.TagRead]) {
        excluded = Set(existing.map { $0.name.lowercased() })
        guard text != self.text else { return }
        self.text = text
        selection = 0
        search()
    }

    func move(_ offset: Int) {
        let count = rows.count
        guard count > 0 else { return }
        selection = min(max(selection + offset, 0), count - 1)
    }

    /// The name Return adds: the highlighted row's, or what is typed.
    var chosenName: String? {
        let rows = rows
        if rows.indices.contains(selection) { return rows[selection].name }
        return trimmed.isEmpty ? nil : trimmed
    }

    func clear() {
        task?.cancel()
        text = ""
        tags = []
        resultsText = nil
        selection = 0
    }

    func settle() async { await task?.value }

    private func search() {
        task?.cancel()
        let prefix = trimmed
        let text = text
        guard !prefix.isEmpty else {
            tags = []
            resultsText = nil
            return
        }
        let client = connection.client
        let debounce = debounce
        task = Task {
            do {
                if debounce > .zero { try await Task.sleep(for: debounce) }
                let query = Operations.ListTags.Input.Query(prefix: prefix, sort: .count, limit: Self.limit)
                let found = try await client.listTags(query: query).ok.body.json
                guard !Task.isCancelled, text == self.text else { return }
                tags = found
                resultsText = text
                selection = 0
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                tags = []
                resultsText = text
                connection.reportFailure(error)
            }
        }
    }
}
