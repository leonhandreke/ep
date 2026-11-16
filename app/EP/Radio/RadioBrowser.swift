import EPAPI
import Foundation
import Observation

/// Where the Radio view is: the selected show → episode → tracklist entry. One entry of
/// the Radio view's navigation history.
nonisolated struct RadioLocation: NavigationLocation {
    var showID: String?
    var episodeID: String?
    var entryID: String?
}

/// One window's Radio view (§6): the shows, the selected show's episodes, the selected
/// episode with its tracklist, and the view's navigation history. Selection flows left
/// to right like the Library's (`LibraryBrowser`); `navigate(to:)` shows a show, an
/// episode or an entry from anywhere else (links, backlinks, search, "Show playing").
@Observable
final class RadioBrowser: HistoryNavigating {
    typealias Phase = LibraryBrowser.Phase

    private(set) var history = NavigationHistory<RadioLocation>()
    var location: RadioLocation { history.current }

    private(set) var shows: [Components.Schemas.RadioShowRead] = []
    private(set) var showsPhase = Phase.idle

    private(set) var show: Components.Schemas.RadioShowDetail?
    private(set) var episodes: [Components.Schemas.RadioEpisodeRead] = []
    private(set) var episodesPhase = Phase.idle

    private(set) var episode: Components.Schemas.RadioEpisodeDetail?
    private(set) var episodePhase = Phase.idle
    /// Re-pointing an entry failed (shown over the tracklist until the next change).
    private(set) var entryError: String?

    /// The tracklist's entries, in tracklist order.
    var entries: [Components.Schemas.RadioEpisodeTrackRead] { episode?.tracks ?? [] }

    var selectedEntry: Components.Schemas.RadioEpisodeTrackRead? {
        guard let id = location.entryID else { return nil }
        return entries.first { $0.id == id }
    }

    /// Set by the window to hand "play from here" to the app's player.
    @ObservationIgnored var onPlay: ((Components.Schemas.RadioEpisodeDetail, String?) -> Void)?

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let clock: () -> ContinuousClock.Instant
    @ObservationIgnored private var showsTask: Task<Void, Never>?
    @ObservationIgnored private var showTask: Task<Void, Never>?
    @ObservationIgnored private var episodeTask: Task<Void, Never>?
    @ObservationIgnored private var navigateTask: Task<Void, Never>?
    @ObservationIgnored private var entryTask: Task<Void, Never>?

    init(connection: ServerConnection, clock: @escaping () -> ContinuousClock.Instant = { .now }) {
        self.connection = connection
        self.clock = clock
    }

    // MARK: - Selection

    func select(show id: String?) {
        guard id != location.showID else { return }
        navigateTask?.cancel()
        go(to: RadioLocation(showID: id), column: .first)
    }

    func select(episode id: String?) {
        guard id != location.episodeID else { return }
        navigateTask?.cancel()
        go(to: RadioLocation(showID: location.showID, episodeID: id), column: .second)
    }

    /// Selecting an entry changes the current history entry, it does not add one.
    func select(entry id: String?) {
        guard id != location.entryID else { return }
        var location = location
        location.entryID = id
        history.replaceCurrent(location)
    }

    // MARK: - History

    var canGoBack: Bool { history.canGoBack }
    var canGoForward: Bool { history.canGoForward }

    func goBack() {
        let before = location
        guard history.back() != nil else { return }
        show(from: before)
    }

    func goForward() {
        let before = location
        guard history.forward() != nil else { return }
        show(from: before)
    }

    // MARK: - Navigation entry point

    /// Shows a radio show, an episode or one of its entries, and records it in the history.
    /// An episode is asked for its show (one request).
    func navigate(to target: OpenTarget) {
        navigateTask?.cancel()
        navigateTask = Task {
            do {
                let location = try await resolve(target)
                guard !Task.isCancelled, let location else { return }
                go(to: location, column: .jump)
            } catch {
                guard !ServerConnection.isCancellation(error) else { return }
                connection.reportFailure(error)
            }
        }
    }

    func resolve(_ target: OpenTarget) async throws -> RadioLocation? {
        switch target.entity.type {
        case .radioShow:
            return RadioLocation(showID: target.entity.id)
        case .radioEpisode:
            let id = target.entity.id
            if let episode, episode.id == id {
                return RadioLocation(showID: episode.showId, episodeID: id, entryID: target.radioEntryID)
            }
            let episode = try await connection.client.getRadioEpisode(path: .init(id: id)).ok.body.json
            return RadioLocation(showID: episode.showId, episodeID: id, entryID: target.radioEntryID)
        case .artist, .releaseGroup, .recording:
            return nil
        }
    }

    // MARK: - Playing and re-pointing

    /// Plays the shown episode from an entry (double-click, Return), or from the start.
    func play(from entryID: String?) {
        guard let episode else { return }
        onPlay?(episode, entryID)
    }

    /// Re-points an entry to another library recording, or unmatches it (nil): the
    /// server moves the entry's slice along. The tracklist is read again afterwards.
    func repoint(entry id: String, to recordingID: String?) {
        entryError = nil
        let client = connection.client
        let previous = entryTask
        entryTask = Task {
            await previous?.value
            do {
                typealias Keys = Components.Schemas.RadioEpisodeTrackUpdate.CodingKeys
                if let recordingID {
                    _ = try await client.updateRadioEpisodeTrack(
                        path: .init(id: id), body: .json(.init(recordingId: recordingID))
                    ).ok
                } else {
                    _ = try await withExplicitNulls(Keys.recordingId) {
                        try await client.updateRadioEpisodeTrack(path: .init(id: id), body: .json(.init())).ok
                    }
                }
                loadEpisode()
                await episodeTask?.value
            } catch {
                guard !ServerConnection.isCancellation(error) else { return }
                entryError = String(localized: "Couldn’t change the entry.")
                connection.reportFailure(error)
            }
        }
    }

    // MARK: - Loading

    /// Reloads what is shown (window activation, reconnect); content stays meanwhile.
    func reload() {
        loadShows()
        loadShow()
        loadEpisode()
    }

    /// Waits for the loads in flight (tests).
    func settle() async {
        await navigateTask?.value
        await entryTask?.value
        await showsTask?.value
        await showTask?.value
        await episodeTask?.value
    }

    private func go(to target: RadioLocation, column: NavigationColumn) {
        let before = location
        history.push(target, column: column, at: clock())
        show(from: before)
    }

    private func show(from before: RadioLocation) {
        if location.showID != before.showID {
            show = nil
            episodes = []
            loadShow()
        }
        if location.episodeID != before.episodeID {
            episode = nil
            entryError = nil
            loadEpisode()
        }
    }

    private func loadShows() {
        showsTask?.cancel()
        if shows.isEmpty { showsPhase = .loading }
        let client = connection.client
        showsTask = Task {
            do {
                let shows = try await allPages { limit, offset in
                    let ok = try await client.listRadioShows(query: .init(limit: limit, offset: offset)).ok
                    return (try ok.body.json, ok.headers.xTotalCount)
                }
                guard !Task.isCancelled else { return }
                self.shows = shows
                showsPhase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                showsPhase = .failed(String(localized: "Couldn’t load shows."))
                connection.reportFailure(error)
            }
        }
    }

    private func loadShow() {
        showTask?.cancel()
        guard let id = location.showID else {
            episodesPhase = .idle
            return
        }
        episodesPhase = .loading
        let client = connection.client
        showTask = Task {
            do {
                async let detail = client.getRadioShow(path: .init(id: id)).ok.body.json
                let episodes = try await allPages { limit, offset in
                    let ok = try await client.listRadioShowEpisodes(
                        path: .init(id: id), query: .init(limit: limit, offset: offset)
                    ).ok
                    return (try ok.body.json, ok.headers.xTotalCount)
                }
                let show = try await detail
                guard !Task.isCancelled, id == location.showID else { return }
                self.show = show
                self.episodes = episodes
                episodesPhase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                episodesPhase = .failed(String(localized: "Couldn’t load episodes."))
                connection.reportFailure(error)
            }
        }
    }

    private func loadEpisode() {
        episodeTask?.cancel()
        guard let id = location.episodeID else {
            episodePhase = .idle
            return
        }
        episodePhase = .loading
        let client = connection.client
        episodeTask = Task {
            do {
                let episode = try await client.getRadioEpisode(path: .init(id: id)).ok.body.json
                guard !Task.isCancelled, id == location.episodeID else { return }
                self.episode = episode
                episodePhase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                episodePhase = .failed(String(localized: "Couldn’t load the tracklist."))
                connection.reportFailure(error)
            }
        }
    }
}

// MARK: - What the rows show

extension Components.Schemas.RadioStation {
    var displayName: String {
        switch self {
        case .nts: "NTS"
        }
    }
}

enum RadioFormat {
    /// "14 Mar 2025".
    static func day(_ date: Date?) -> String? {
        date?.formatted(.dateTime.day().month(.abbreviated).year())
    }

    /// "14 Mar 2025 · London".
    static func episodeMeta(_ episode: Components.Schemas.RadioEpisodeRead) -> String {
        [day(episode.broadcastAt), episode.location].compactMap { $0?.isEmpty == false ? $0 : nil }
            .joined(separator: " · ")
    }

    /// "31 entries · 2:00:00 · 22 matched".
    static func tracklistMeta(_ episode: Components.Schemas.RadioEpisodeDetail) -> String {
        let count = episode.tracks.count
        let matched = episode.tracks.filter { $0.recording != nil }.count
        return [
            count == 1 ? String(localized: "1 entry") : String(localized: "\(count) entries"),
            LibraryFormat.duration(ms: episode.durationMs),
            String(localized: "\(matched) matched"),
        ]
        .compactMap(\.self).joined(separator: " · ")
    }

    /// The entry's time in the episode: "28:02", "~58:40" when estimated; "" without one.
    static func time(_ entry: Components.Schemas.RadioEpisodeTrackRead) -> String {
        guard let time = LibraryFormat.duration(ms: entry.startMs) else { return "" }
        return entry.offsetIsEstimate ? "~" + time : time
    }

    /// The state column: "in library", "only this excerpt", "not matched", plus
    /// " · est. time" for an estimated time. "in library" says whether the library plays
    /// it from a file of its own.
    static func state(_ entry: Components.Schemas.RadioEpisodeTrackRead) -> String {
        let state: String =
            switch entry.state {
            case .inLibrary:
                entry.libraryPlayback != nil ? String(localized: "in library") : String(localized: "in library, no file")
            case .excerptOnly: String(localized: "only this excerpt")
            case .notMatched: String(localized: "not matched")
            }
        return entry.offsetIsEstimate && entry.startMs != nil ? state + String(localized: " · est. time") : state
    }

    /// The row tooltip.
    static func help(_ entry: Components.Schemas.RadioEpisodeTrackRead) -> String {
        switch entry.state {
        case .inLibrary:
            entry.libraryPlayback != nil
                ? String(localized: "Matched to a library recording that plays from its own file")
                : String(localized: "Matched to a library recording with no file of its own")
        case .excerptOnly: String(localized: "Matched to a recording known only from radio")
        case .notMatched: String(localized: "Not matched")
        }
    }
}
