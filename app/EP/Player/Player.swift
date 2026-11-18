import AppKit
import EPAPI
import Foundation
import Observation

/// The app's one player, shared by every window: what plays, what comes next, and the
/// transport. Library windows ask it to play through `LibraryBrowser.play(_:)`; the Radio
/// view plays an episode with `playEpisode(_:from:)`.
///
/// Times are milliseconds in the file (`positionMS`), as the API gives ranges.
@Observable
final class Player {
    /// Why the bar shows a message instead of what plays (board 3, "tried to play").
    enum Notice: Equatable {
        case linkOnly(title: String, kind: Components.Schemas.SourceKind, url: URL)
        case fileMissing(title: String)
        case noSource(title: String)
        case failed(title: String, message: String)
    }

    private(set) var current: PlayItem?
    /// Up next, in order.
    private(set) var queue: [PlayItem] = []
    private(set) var notice: Notice?
    private(set) var isPlaying = false
    /// The current item's file is being opened.
    private(set) var isLoading = false
    private(set) var positionMS = 0
    /// 0…1; kept across launches in the defaults the player was given.
    var volume: Float = 1 {
        didSet {
            engine.volume = volume
            defaults?.set(volume, forKey: Self.volumeKey)
        }
    }

    /// What played before the current item (Previous).
    @ObservationIgnored private(set) var history: [PlayItem] = []
    @ObservationIgnored var nowPlaying: NowPlayingCenter?
    @ObservationIgnored private let engine: any PlaybackEngine
    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let tickInterval: Duration
    @ObservationIgnored private let defaults: UserDefaults?
    static let volumeKey = "volume"
    @ObservationIgnored private var loadTask: Task<Void, Never>?
    @ObservationIgnored private var requestTask: Task<Void, Never>?
    @ObservationIgnored private var tickTask: Task<Void, Never>?
    @ObservationIgnored private var shownEntryID: String?

    /// Previous goes to the start of what plays unless it began less than this ago.
    static let restartThresholdMS = 3000

    init(
        connection: ServerConnection, engine: any PlaybackEngine, tickInterval: Duration = .milliseconds(100),
        defaults: UserDefaults? = nil
    ) {
        self.connection = connection
        self.engine = engine
        self.tickInterval = tickInterval
        self.defaults = defaults
        if let saved = defaults?.object(forKey: Self.volumeKey) as? Float {
            volume = min(max(saved, 0), 1)
            engine.volume = volume
        }
        engine.onEnd = { [weak self] in self?.advance() }
        engine.onFailure = { [weak self] message in self?.fail(message) }
    }

    // MARK: - Starting

    /// Plays what a Library window asks for: a track (and the rest of its list as the
    /// queue) or a whole album. A track that does not play shows why.
    func play(_ request: LibraryBrowser.PlayRequest) {
        requestTask?.cancel()
        switch request {
        case .track(let row, let rows, let album):
            guard row.playability.playback != nil else { return refuse(row) }
            if let index = rows.firstIndex(where: { $0.id == row.id }) {
                start(PlayItem.queue(from: rows, startingAt: index, album: album))
            } else {
                start(PlayItem.queue(from: [row], startingAt: 0, album: album))
            }
        case .album(let id):
            play(EntityRef(type: .releaseGroup, id: id))
        }
    }

    /// Plays an album, a track (recording) or an episode found in search (⌘↩), or puts it
    /// next in the queue (⌥↩; with nothing playing, it plays). An episode found by what it
    /// played starts at that entry. Artists and shows do not play.
    func play(_ ref: EntityRef, from entryID: String? = nil, next: Bool = false) {
        requestTask?.cancel()
        let client = connection.client
        requestTask = Task {
            do {
                let items: [PlayItem]
                switch ref.type {
                case .releaseGroup:
                    async let detail = client.getReleaseGroup(path: .init(id: ref.id)).ok.body.json
                    let tracks = try await allPages { limit, offset in
                        let ok = try await client.listReleaseGroupTracks(
                            path: .init(id: ref.id), query: .init(limit: limit, offset: offset)
                        ).ok
                        return (try ok.body.json, ok.headers.xTotalCount)
                    }
                    let group = try await detail
                    let rows = tracks.map { TrackRow(track: $0, albumCreditID: group.artistCredit.id) }
                    items = PlayItem.queue(from: rows, startingAt: 0, album: group)
                case .recording:
                    let row = TrackRow(recording: try await client.getRecording(path: .init(id: ref.id)).ok.body.json)
                    guard !Task.isCancelled else { return }
                    guard row.playability.playback != nil else {
                        if !next { refuse(row) }
                        return
                    }
                    items = PlayItem.queue(from: [row], startingAt: 0, album: nil)
                case .radioEpisode:
                    let episode = try await client.getRadioEpisode(path: .init(id: ref.id)).ok.body.json
                    guard !Task.isCancelled else { return }
                    guard let item = PlayItem(episode: episode, from: entryID) else {
                        if !next { show(.noSource(title: episode.name)) }
                        return
                    }
                    items = [item]
                case .artist, .radioShow:
                    return
                }
                guard !Task.isCancelled, !items.isEmpty else { return }
                if next, current != nil {
                    queue.insert(contentsOf: items, at: 0)
                } else {
                    start(items)
                }
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                connection.reportFailure(error)
            }
        }
    }

    /// Whether `play(_:)` can play this kind of entity.
    static func plays(_ type: Components.Schemas.EntityType) -> Bool {
        [.releaseGroup, .recording, .radioEpisode].contains(type)
    }

    /// Waits for a play request being loaded (tests).
    func settle() async { await requestTask?.value }

    /// Plays a radio episode from one of its tracklist entries (or the start): the
    /// Radio view's "play from here". The queue is left as it is.
    func playEpisode(_ episode: Components.Schemas.RadioEpisodeDetail, from entryID: String? = nil) {
        requestTask?.cancel()
        guard let item = PlayItem(episode: episode, from: entryID) else {
            return show(.noSource(title: episode.name))
        }
        if let current { history.append(current) }
        begin(item)
    }

    /// Replaces what plays and the queue.
    private func start(_ items: [PlayItem]) {
        guard let first = items.first else { return }
        history = []
        queue = Array(items.dropFirst())
        begin(first)
    }

    private func refuse(_ row: TrackRow) {
        switch row.playability.state {
        case .linkOnly:
            if let link = row.playability.link, let url = URL(string: link.url) {
                show(.linkOnly(title: row.title, kind: link.kind, url: url))
            } else {
                show(.noSource(title: row.title))
            }
        case .fileMissing: show(.fileMissing(title: row.title))
        default: show(.noSource(title: row.title))
        }
    }

    /// Stops and shows `notice` in place of what played; the queue goes too.
    private func show(_ notice: Notice) {
        stopEngine()
        current = nil
        queue = []
        history = []
        self.notice = notice
        nowPlaying?.update(self)
    }

    func dismissNotice() { notice = nil }

    private func begin(_ item: PlayItem, at ms: Int? = nil) {
        stopEngine()
        current = item
        notice = nil
        isLoading = true
        positionMS = ms ?? item.playFromMS
        shownEntryID = item.entry(at: positionMS + PlayItem.tolerance)?.id
        let url = connection.serverURL.appending(path: "sources/\(item.fileSourceID)/stream")
        let start = Double(positionMS) / 1000
        loadTask = Task {
            do {
                try await engine.load(url, mediaType: item.mediaType, at: start)
                guard !Task.isCancelled, current?.id == item.id else { return }
                isLoading = false
                resume()
            } catch {
                guard !Task.isCancelled, current?.id == item.id, !(error is CancellationError),
                    !ServerConnection.isCancellation(error)
                else { return }
                connection.reportFailure(error)
                fail(Self.describe(error))
            }
        }
        if case .excerpt(let episodeID) = item.kind { attachEpisode(episodeID, to: item.id) }
        nowPlaying?.update(self)
    }

    /// Loads the episode of a radio excerpt, for its scrubber, marks and context.
    private func attachEpisode(_ episodeID: String, to itemID: UUID) {
        let client = connection.client
        Task {
            guard let episode = try? await client.getRadioEpisode(path: .init(id: episodeID)).ok.body.json,
                current?.id == itemID
            else { return }
            current?.attach(episode)
            nowPlaying?.update(self)
        }
    }

    private func fail(_ message: String) {
        show(.failed(title: current.map { displayTitle(of: $0) } ?? "", message: message))
    }

    private static func describe(_ error: any Error) -> String {
        if let failure = error as? HTTPRangeSource.Failure, case .status(let status) = failure {
            return status == 404
                ? String(localized: "The server has no such file.")
                : String(localized: "The server answered \(status).")
        }
        return (error as? LocalizedError)?.errorDescription ?? String(describing: error)
    }

    // MARK: - Transport

    func togglePlayPause() {
        if isPlaying { pause() } else { resume() }
    }

    func resume() {
        guard current != nil, !isLoading else { return }
        engine.play()
        isPlaying = true
        tickTask?.cancel()
        tickTask = Task { [tickInterval] in
            while !Task.isCancelled {
                tick()
                try? await Task.sleep(for: tickInterval)
            }
        }
        nowPlaying?.update(self)
    }

    func pause() {
        guard isPlaying else { return }
        engine.pause()
        isPlaying = false
        tickTask?.cancel()
        tick()
        nowPlaying?.update(self)
    }

    /// Moves within the current item; kept inside its span.
    func seek(to ms: Int) {
        guard let current, !isLoading else { return }
        let upper = current.spanEndMS ?? Int.max
        positionMS = min(max(ms, current.startMS), max(upper - 1, current.startMS))
        engine.seek(to: Double(positionMS) / 1000)
        noteEntryChange()
        nowPlaying?.update(self)
    }

    /// The next entry of a long file, else the next item.
    func next() {
        guard let current else { return }
        if let entry = current.entry(after: positionMS) {
            seek(to: entry.startMS)
        } else {
            advance()
        }
    }

    /// The start of the current entry or item, or the one before if it began less than
    /// three seconds ago.
    func previous() {
        guard let current else { return }
        let position = positionMS
        if let entry = current.entry(at: position + PlayItem.tolerance) {
            if position - entry.startMS > Self.restartThresholdMS { return seek(to: entry.startMS) }
            if let before = current.entry(before: entry.startMS) { return seek(to: before.startMS) }
        } else if let before = current.entry(before: position) {
            return seek(to: before.startMS)
        }
        guard position - current.startMS <= Self.restartThresholdMS, let previous = history.popLast() else {
            return seek(to: current.startMS)
        }
        queue.insert(current, at: 0)
        begin(previous, at: previous.entries.last { $0.startMS >= previous.startMS }?.startMS ?? previous.startMS)
    }

    /// What the current item's span ended (or Next past its last entry): the next item,
    /// or at the end of the queue, the current item paused at its start.
    func advance() {
        guard let current else { return }
        if !queue.isEmpty {
            history.append(current)
            begin(queue.removeFirst())
        } else {
            pause()
            positionMS = current.startMS
            engine.seek(to: Double(current.startMS) / 1000)
            noteEntryChange()
            nowPlaying?.update(self)
        }
    }

    /// Reads the engine's time; ends the item at the end of its span.
    func tick() {
        guard let current, !isLoading else { return }
        positionMS = Int((engine.currentTime * 1000).rounded())
        if isPlaying, let end = current.endMS, positionMS >= end {
            return advance()
        }
        noteEntryChange()
    }

    /// The title follows the entry: tell Now Playing when it changes.
    private func noteEntryChange() {
        let entryID = current?.entry(at: positionMS + PlayItem.tolerance)?.id
        guard entryID != shownEntryID else { return }
        shownEntryID = entryID
        nowPlaying?.update(self)
    }

    private func stopEngine() {
        loadTask?.cancel()
        tickTask?.cancel()
        engine.stop()
        isPlaying = false
        isLoading = false
    }

    // MARK: - Queue

    func removeFromQueue(_ id: PlayItem.ID) {
        queue.removeAll { $0.id == id }
    }

    func clearQueue() {
        queue = []
    }

    // MARK: - What the bar shows

    /// The entry of a long file playing now.
    var currentEntry: PlayItem.Entry? {
        current?.entry(at: positionMS + PlayItem.tolerance)
    }

    /// The title: the current entry's, else the item's.
    var title: String { current.map(displayTitle(of:)) ?? "" }

    private func displayTitle(of item: PlayItem) -> String {
        item.entry(at: positionMS + PlayItem.tolerance)?.title ?? item.title
    }

    /// "Björk — Homogenic"; for an episode entry, its artist.
    var subtitle: String {
        guard let current else { return "" }
        guard let entry = currentEntry else { return current.subtitle }
        switch current.kind {
        case .singleFile(let album, _): return PlayItem.subtitle(artist: entry.artist, album: album)
        case .episode, .excerpt: return entry.artist
        case .file: return current.subtitle
        }
    }

    /// `subtitle` in parts, each with where a click on it goes: the artist and album (an
    /// episode: its show; an episode entry: its artist).
    var subtitleLinks: [PlayerLink] {
        guard let current else { return [] }
        guard let entry = currentEntry else { return current.subtitleLinks }
        switch current.kind {
        case .singleFile(let album, _):
            return [
                PlayerLink(text: entry.artist, target: entry.artistTarget),
                PlayerLink(text: album, target: current.albumTarget),
            ]
        case .episode, .excerpt: return [PlayerLink(text: entry.artist, target: entry.artistTarget)]
        case .file: return current.subtitleLinks
        }
    }

    /// "also in library as a file" after an episode entry's artist.
    var subtitleNote: String? { current?.isEpisode == true ? currentEntry?.note : nil }

    /// The line above the title: "Track 4 of 14 · one file", "Late Night Tapes · 14 Mar
    /// 2025 · 7 of 31".
    var context: String? {
        guard let current else { return nil }
        let entry = currentEntry
        switch current.kind {
        case .file, .excerpt:
            return nil
        case .singleFile(_, let count):
            return entry.map { String(localized: "Track \($0.number) of \(count) · one file") }
                ?? String(localized: "\(count) tracks · one file")
        case .episode(let episode):
            return [
                episode.show, episode.date, entry.map { String(localized: "\($0.number) of \(episode.entryCount)") },
            ]
            .compactMap(\.self).joined(separator: " · ")
        }
    }

    /// Where the title and "Show playing" (⌘L) go: the current entry's recording or
    /// track, else the item's. Nil for an episode outside a matched entry.
    var libraryTarget: LibraryEntity? {
        if let entry = currentEntry { return entry.target }
        return current?.target
    }

    /// Where "Show playing" goes for an episode played from the Radio view: the episode
    /// at the entry playing now. Nil for anything played from the Library (a radio excerpt
    /// of a track included): that is shown in the Library (`libraryTarget`).
    var radioTarget: OpenTarget? {
        guard let current, current.target == nil, case .episode(let episode) = current.kind else { return nil }
        return OpenTarget(entity: EntityRef(type: .radioEpisode, id: episode.id), radioEntryID: currentEntry?.id)
    }

    /// Whether "Show playing" has somewhere to go.
    var canShowPlaying: Bool { radioTarget != nil || libraryTarget != nil }

    /// Whether a tracks-column row is what plays (the now-playing bars).
    func isPlaying(_ row: TrackRow) -> Bool {
        switch libraryTarget {
        case .track(let id): row.number != nil && row.id == id
        case .recording(let id): row.recordingID == id
        default: false
        }
    }

    /// The scrubber's scale in file time: the whole file for long files and ranges, the
    /// track's span for a track from its own file.
    var scrubberRange: ClosedRange<Int> {
        guard let current else { return 0...0 }
        let end = current.coversWholeFile ? (current.durationMS ?? current.spanEndMS) : current.spanEndMS
        let lower = current.coversWholeFile ? 0 : current.startMS
        return lower...max(end ?? positionMS, lower)
    }

    var elapsedMS: Int { positionMS - scrubberRange.lowerBound }
    var remainingMS: Int { max(scrubberRange.upperBound - positionMS, 0) }
}
