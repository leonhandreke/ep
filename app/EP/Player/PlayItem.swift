import EPAPI
import Foundation

/// One thing in the player: a span of one file on the server (`GET /sources/{id}/stream`).
///
/// A track from its own file is the whole file. A single-file album is one item for all
/// its tracks (consecutive ranges of one file): the scrubber covers the file, each track
/// is an entry, and playing goes on from one to the next. A radio episode is one item
/// with its tracklist as entries. A radio excerpt plays its range of the episode, shown
/// on the episode's scrubber once the episode is loaded.
struct PlayItem: Identifiable, Equatable {
    let id = UUID()
    var kind: Kind
    /// The item's own title and second line; a current entry's take their place.
    var title: String
    var subtitle: String
    var coverPath: String?
    /// What the title and "Show playing" navigate to when no entry says otherwise.
    var target: LibraryEntity?
    var fileSourceID: String
    /// The second line in parts (artist, album; an episode's show), each with where a
    /// click on it goes. An entry playing replaces the artist (`Entry.artistTarget`).
    var subtitleLinks: [PlayerLink] = []
    /// The album a single-file album's entries belong to.
    var albumTarget: OpenTarget?
    /// The file's media type (`audio/ogg`, …): which engine plays it.
    var mediaType: String
    /// The scrubber's scale (`file_duration_ms`), else the recording's length.
    var durationMS: Int?
    /// The span that plays: `startMS` to `endMS` (nil: the end of the file).
    var startMS: Int
    var endMS: Int?
    /// Where playing begins inside the span (the chosen track of a single-file album, the
    /// chosen entry of an episode).
    var playFromMS: Int
    /// Ranges of the file in order of start: tracks of a single-file album, an episode's
    /// timed tracklist entries.
    var entries: [Entry] = []

    enum Kind: Equatable {
        /// A track from its own file.
        case file
        /// Tracks of a single-file album: "Track 4 of 14 · one file".
        case singleFile(album: String, trackCount: Int)
        /// A radio episode or an excerpt of one.
        case episode(Episode)
        /// A radio excerpt whose episode (`episodeID`) is still loading.
        case excerpt(episodeID: String)
    }

    struct Episode: Equatable {
        var id: String
        var name: String
        var show: String
        var date: String?
        /// All tracklist entries, timed or not ("7 of 31").
        var entryCount: Int
    }

    struct Entry: Equatable, Identifiable {
        var id: String
        var startMS: Int
        var endMS: Int?
        /// The time is an estimate (hollow mark, "~").
        var isEstimated = false
        var title: String
        var artist: String
        /// Track number, or the entry's position in the tracklist.
        var number: Int
        var target: LibraryEntity?
        /// "also in library as a file" for radio entries.
        var note: String?
        /// Where a click on the artist goes.
        var artistTarget: OpenTarget?
    }

    var isEpisode: Bool {
        switch kind {
        case .episode, .excerpt: true
        default: false
        }
    }

    // MARK: - Entries

    /// The entry playing at `ms`: the last one started, unless it has ended (a gap).
    func entry(at ms: Int) -> Entry? {
        guard let entry = entries.last(where: { $0.startMS <= ms }) else { return nil }
        if let end = entry.endMS, ms >= end { return nil }
        return entry
    }

    /// Engines report a time a little before where they were sent; this much counts as
    /// being there.
    static let tolerance = 250

    /// The entry after the one playing at `ms` (or after `ms` in a gap), inside the span.
    func entry(after ms: Int) -> Entry? {
        let reference = entry(at: ms + Self.tolerance)?.startMS ?? ms
        guard let next = entries.first(where: { $0.startMS > reference }) else { return nil }
        if let endMS, next.startMS >= endMS { return nil }
        return next
    }

    /// The last entry starting before `ms`, inside the span.
    func entry(before ms: Int) -> Entry? {
        entries.last { $0.startMS < ms && $0.startMS >= startMS }
    }

    /// The scrubber covers the whole file, except for a track from its own file, where it
    /// is the track's span.
    var coversWholeFile: Bool { kind != .file }

    /// The end of the span in file time, if known.
    var spanEndMS: Int? { endMS ?? durationMS }
}

// MARK: - Building items

extension PlayItem {
    /// What plays when a track is chosen in a list: that track, then the rest of the list
    /// (the album) as the queue. Rows that do not play are left out. Consecutive ranges of
    /// one file (a single-file album) are one item, starting at the chosen track.
    static func queue(
        from rows: [TrackRow], startingAt index: Int, album: Components.Schemas.ReleaseGroupDetail?
    ) -> [PlayItem] {
        guard rows.indices.contains(index) else { return [] }
        // Rows playing a range of a file that other rows here share.
        var groups: [String: [TrackRow]] = [:]
        for row in rows where row.playability.state == .slice {
            if let playback = row.playability.playback { groups[playback.fileSourceId, default: []].append(row) }
        }
        groups = groups.filter { $0.value.count > 1 }

        var items: [PlayItem] = []
        var emitted = Set<String>()
        for row in rows[index...] {
            guard let playback = row.playability.playback else { continue }
            if row.playability.state == .slice, let group = groups[playback.fileSourceId] {
                guard emitted.insert(playback.fileSourceId).inserted else { continue }
                items.append(singleFile(group, from: row, album: album, trackCount: rows.count))
            } else {
                items.append(PlayItem(row: row, playback: playback, album: album, trackCount: rows.count))
            }
        }
        return items
    }

    /// One row on its own.
    init(
        row: TrackRow, playback: Components.Schemas.PlaybackRead, album: Components.Schemas.ReleaseGroupDetail?,
        trackCount: Int
    ) {
        let kind: Kind
        var entries: [Entry] = []
        if let episodeID = row.playability.radioEntry?.episodeId {
            kind = .excerpt(episodeID: episodeID)
        } else if row.playability.state == .slice {
            kind = .singleFile(album: album?.title ?? row.title, trackCount: album?.trackCount ?? trackCount)
            entries = [Entry(row: row, playback: playback)]
        } else {
            kind = .file
        }
        let albumTarget = album.map { OpenTarget(entity: EntityRef(type: .releaseGroup, id: $0.id)) }
        self.init(
            kind: kind, title: row.title, subtitle: Self.subtitle(artist: row.artistName, album: album?.title),
            coverPath: album?.coverUrl, target: row.libraryEntity, fileSourceID: playback.fileSourceId,
            subtitleLinks: [PlayerLink(text: row.artistName, target: row.artistTarget)]
                + (album.map { [PlayerLink(text: $0.title, target: albumTarget)] } ?? []),
            albumTarget: albumTarget, mediaType: playback.mediaType,
            durationMS: playback.fileDurationMs ?? row.lengthMS, startMS: playback.startMs, endMS: playback.endMs,
            playFromMS: playback.startMs, entries: entries)
    }

    private static func singleFile(
        _ group: [TrackRow], from row: TrackRow, album: Components.Schemas.ReleaseGroupDetail?, trackCount: Int
    ) -> PlayItem {
        let entries = group.compactMap { row in row.playability.playback.map { Entry(row: row, playback: $0) } }
            .sorted { $0.startMS < $1.startMS }
        let playback = row.playability.playback!
        let last = group.compactMap(\.playability.playback).max { $0.startMs < $1.startMs }
        return PlayItem(
            kind: .singleFile(album: album?.title ?? row.title, trackCount: album?.trackCount ?? trackCount),
            title: album?.title ?? row.title, subtitle: row.artistName, coverPath: album?.coverUrl,
            target: row.libraryEntity, fileSourceID: playback.fileSourceId,
            subtitleLinks: [PlayerLink(text: row.artistName, target: row.artistTarget)],
            albumTarget: album.map { OpenTarget(entity: EntityRef(type: .releaseGroup, id: $0.id)) },
            mediaType: playback.mediaType,
            durationMS: playback.fileDurationMs, startMS: entries.first?.startMS ?? playback.startMs,
            endMS: last?.endMs, playFromMS: playback.startMs, entries: entries)
    }

    /// A radio episode, from the chosen entry (or the start). Nil when it has no audio.
    init?(episode: Components.Schemas.RadioEpisodeDetail, from entryID: String? = nil) {
        guard let playback = episode.playback else { return nil }
        let entries = Self.entries(of: episode)
        let start = entryID.flatMap { id in entries.first { $0.id == id }?.startMS } ?? playback.startMs
        self.init(
            kind: .episode(Self.episodeInfo(episode)), title: episode.name, subtitle: episode.show.name,
            coverPath: episode.imageUrl, target: nil, fileSourceID: playback.fileSourceId,
            subtitleLinks: [
                PlayerLink(
                    text: episode.show.name,
                    target: OpenTarget(entity: EntityRef(type: .radioShow, id: episode.showId)))
            ],
            mediaType: playback.mediaType,
            durationMS: episode.durationMs ?? playback.fileDurationMs, startMS: playback.startMs,
            endMS: playback.endMs, playFromMS: start, entries: entries)
    }

    /// An excerpt item once its episode has loaded: the episode's scrubber, marks and
    /// context; the span stays the excerpt's.
    mutating func attach(_ episode: Components.Schemas.RadioEpisodeDetail) {
        kind = .episode(Self.episodeInfo(episode))
        entries = Self.entries(of: episode)
        coverPath = episode.imageUrl
        durationMS = episode.durationMs ?? durationMS
    }

    private static func episodeInfo(_ episode: Components.Schemas.RadioEpisodeDetail) -> Episode {
        Episode(
            id: episode.id, name: episode.name, show: episode.show.name,
            date: episode.broadcastAt?.formatted(.dateTime.day().month(.abbreviated).year()),
            entryCount: episode.tracks.count)
    }

    private static func entries(of episode: Components.Schemas.RadioEpisodeDetail) -> [Entry] {
        episode.tracks.compactMap { track -> Entry? in
            guard let start = track.startMs else { return nil }
            let recording = track.recording
            let note: String? =
                track.libraryPlayback != nil
                ? String(localized: "also in library as a file")
                : track.state == .inLibrary ? String(localized: "also in library") : nil
            return Entry(
                id: track.id, startMS: start, endMS: track.endMs, isEstimated: track.offsetIsEstimate,
                title: recording?.title ?? track.titleText, artist: recording?.artistCredit.name ?? track.artistText,
                number: track.position, target: track.state == .inLibrary ? recording.map { .recording($0.id) } : nil,
                note: note,
                artistTarget: recording?.artistCredit.names.first.map {
                    OpenTarget(entity: EntityRef(type: .artist, id: $0.artistId))
                })
        }
        .sorted { $0.startMS < $1.startMS }
    }

    static func subtitle(artist: String, album: String?) -> String {
        album.map { "\(artist) — \($0)" } ?? artist
    }
}

extension PlayItem.Entry {
    init(row: TrackRow, playback: Components.Schemas.PlaybackRead) {
        self.init(
            id: row.id, startMS: playback.startMs, endMS: playback.endMs, title: row.title, artist: row.artistName,
            number: row.number ?? 1, target: row.libraryEntity, artistTarget: row.artistTarget)
    }
}

extension TrackRow {
    /// What the row is in the Library: a track on an album, or a loose recording.
    var libraryEntity: LibraryEntity { number == nil ? .recording(recordingID) : .track(id) }

    /// The row's (first credited) artist.
    var artistTarget: OpenTarget? { artistID.map { OpenTarget(entity: EntityRef(type: .artist, id: $0)) } }
}

/// A part of the player's second line (artist, album, show) and where a click on it goes
/// (nil: nowhere).
struct PlayerLink: Equatable {
    var text: String
    var target: OpenTarget?
}
