import EPAPI
import Foundation
import Observation

/// What one detail panel shows for the entity selected at its level (§3.7): its note,
/// tags, the notes that link to it and the radio episodes that played it, from
/// `GET /entity-details` in one request. The Library has three (artist, album, track);
/// the Radio view hosts its sections in a tabbed panel (stage 7).
///
/// A track's note and tags are its recording's (`ref` is the recording), so they follow
/// it to every album and radio excerpt.
@Observable
final class EntityDetails {
    enum Phase: Equatable {
        case idle, loading, loaded
        case failed(String)
    }

    private(set) var ref: EntityRef?
    private(set) var phase = Phase.idle
    private(set) var entity: Components.Schemas.EntityRead?
    private(set) var note: NoteDocument?
    private(set) var tags: [Components.Schemas.TagRead] = []
    private(set) var backlinks: [Backlink] = []
    private(set) var radioAppearances: [RadioAppearance] = []
    /// A tag change failed (shown under the tags until the next change).
    private(set) var tagError: String?

    /// Called after this panel's note was saved (other panels may now be linked from it).
    @ObservationIgnored var onNoteSaved: (() -> Void)?

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let noteDebounce: Duration
    @ObservationIgnored private var loadTask: Task<Void, Never>?
    @ObservationIgnored private var tagTask: Task<Void, Never>?

    init(connection: ServerConnection, noteDebounce: Duration = .seconds(1)) {
        self.connection = connection
        self.noteDebounce = noteDebounce
    }

    /// Shows another entity (or nothing). An unsaved note edit is saved first.
    func show(_ ref: EntityRef?) {
        guard ref != self.ref else { return }
        note?.flush()
        // An edit that could not be saved yet stays in `NoteDocument.unsaved` and comes
        // back when this entity is shown again.
        note?.isShown = false
        self.ref = ref
        entity = nil
        note = nil
        tags = []
        backlinks = []
        radioAppearances = []
        tagError = nil
        load()
    }

    /// Loads again (window activation, reconnect, another panel's note saved). What is
    /// shown stays meanwhile; an edited note is kept and saved.
    func reload() {
        note?.flush()
        load()
    }

    /// Waits for the loads in flight (tests).
    func settle() async {
        await loadTask?.value
        await tagTask?.value
    }

    private func load() {
        loadTask?.cancel()
        guard let ref else {
            phase = .idle
            return
        }
        if entity == nil { phase = .loading }
        let client = connection.client
        loadTask = Task {
            do {
                let details = try await client.getEntityDetails(path: .init(_type: ref.type, id: ref.id)).ok.body.json
                guard !Task.isCancelled, ref == self.ref else { return }
                apply(details)
                phase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error), ref == self.ref else { return }
                if entity == nil { phase = .failed(String(localized: "Couldn’t load details.")) }
                connection.reportFailure(error)
            }
        }
    }

    private func apply(_ details: Components.Schemas.EntityDetailsRead) {
        entity = details.entity
        tags = details.tags
        backlinks = details.backlinks.map(Backlink.init)
        radioAppearances = details.radioAppearances.map(RadioAppearance.init)
        if let note, note.ref == ref {
            note.apply(details.note)
        } else if let ref {
            let document: NoteDocument
            if let orphan = NoteDocument.orphan(for: ref, connection: connection) {
                document = orphan
                document.apply(details.note)
            } else {
                document = NoteDocument(details.note, ref: ref, connection: connection, debounce: noteDebounce)
            }
            document.isShown = true
            document.onSaved = { [weak self] in self?.onNoteSaved?() }
            note = document
        }
    }

    // MARK: - Tags

    /// Adds a tag by name (found or created on the server, ignoring case).
    func addTag(_ name: String) {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let ref, !name.isEmpty else { return }
        let client = connection.client
        runTagChange {
            let tags = try await client.addEntityTag(
                path: .init(_type: ref.type, id: ref.id), body: .json(.init(name: name))
            ).ok.body.json
            guard ref == self.ref else { return }
            self.tags = tags
        }
    }

    func removeTag(_ tag: Components.Schemas.TagRead) {
        guard let ref else { return }
        let client = connection.client
        runTagChange {
            let tags = try await client.removeEntityTag(path: .init(_type: ref.type, id: ref.id, tagId: tag.id)).ok
                .body.json
            guard ref == self.ref else { return }
            self.tags = tags
        }
    }

    private func runTagChange(_ change: @escaping () async throws -> Void) {
        tagError = nil
        let previous = tagTask
        tagTask = Task {
            await previous?.value
            do {
                try await change()
            } catch {
                guard !ServerConnection.isCancellation(error) else { return }
                tagError = String(localized: "Couldn’t change the tags.")
                connection.reportFailure(error)
            }
        }
    }

    // MARK: - Radio

    /// The episode an appearance is in, for playing it from there.
    func episode(of appearance: RadioAppearance) async -> Components.Schemas.RadioEpisodeDetail? {
        do {
            return try await connection.client.getRadioEpisode(path: .init(id: appearance.episodeID)).ok.body.json
        } catch {
            connection.reportFailure(error)
            return nil
        }
    }
}

/// A note that links here (Linked from): whose note it is and the lines around the links,
/// with the link text to highlight (UTF-16 ranges into each snippet).
struct Backlink: Identifiable, Equatable {
    var source: EntityRef
    var kind: String
    var title: String
    var snippets: [Snippet]

    var id: EntityRef { source }

    struct Snippet: Equatable {
        var text: String
        var highlights: [NSRange]
    }

    init(_ read: Components.Schemas.BacklinkRead) {
        source = EntityRef(type: read.source._type, id: read.source.id)
        kind = read.source._type.kindLabel
        title = read.source.title
        snippets = read.snippets.map { snippet in
            let length = (snippet.text as NSString).length
            return Snippet(
                text: snippet.text,
                highlights: snippet.highlights.compactMap { range in
                    // API ranges are UTF-16 offsets: NSRange as is, clamped to the text.
                    let start = min(max(range.start, 0), length), end = min(max(range.end, start), length)
                    return end > start ? NSRange(location: start, length: end - start) : nil
                })
        }
    }
}

/// A radio episode that played this (Linked from): "{show} — {date}" over what played,
/// with the time position in the episode ("~" and italic when estimated).
struct RadioAppearance: Identifiable, Equatable {
    var id: String
    var episodeID: String
    var heading: String
    var played: String
    /// "28:02", "~1:12:40"; nil when the entry has no time.
    var position: String?
    var isEstimated: Bool
    /// The entry has a time in a file that plays.
    var canPlay: Bool

    init(_ read: Components.Schemas.RadioAppearanceRead) {
        id = read.trackId
        episodeID = read.episode.id
        let date = read.episode.broadcastAt?.formatted(.dateTime.day().month(.abbreviated).year())
        heading = [read.show.name, date].compactMap(\.self).joined(separator: " — ")
        played = read.recording.title
        isEstimated = read.offsetIsEstimate
        position = LibraryFormat.duration(ms: read.startMs).map { read.offsetIsEstimate ? "~" + $0 : $0 }
        canPlay = read.playback != nil && read.startMs != nil
    }
}
