import EPAPI
import Foundation
import Observation

/// One window's Library selection and what it shows (§3.3–3.4): the selected artist with
/// its release groups, the selected album (or loose tracks) with its tracks, and the
/// window's navigation history (decision 13: each window has its own).
///
/// Selection flows left to right: choosing an artist clears the album and track,
/// choosing an album clears the track. Everything that changes the location goes through
/// `go(to:column:)`; `navigate(to:)` is the entry point for everything else that wants to
/// show an entity (back/forward, links, search, "Show playing").
@Observable
final class LibraryBrowser {
    enum Phase: Equatable {
        case idle, loading, loaded
        case failed(String)
    }

    /// What double-click / Return / Play album asks the player for (stage 4).
    enum PlayRequest: Equatable {
        case track(TrackRow, in: [TrackRow])
        case album(String)
    }

    private(set) var history = NavigationHistory()
    var location: LibraryLocation { history.current }

    private(set) var artist: Components.Schemas.ArtistDetail?
    private(set) var releaseGroups: [Components.Schemas.ReleaseGroupRead] = []
    private(set) var releaseGroupsPhase = Phase.idle

    private(set) var releaseGroup: Components.Schemas.ReleaseGroupDetail?
    private(set) var tracks: [TrackRow] = []
    private(set) var tracksPhase = Phase.idle

    /// The selected track's row, for the track panel's header.
    var selectedTrack: TrackRow? {
        guard let id = location.trackID else { return nil }
        return tracks.first { $0.id == id }
    }

    /// The stage 4 hook: the player sets this to play what the user asked for.
    @ObservationIgnored var onPlay: ((PlayRequest) -> Void)?

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let clock: () -> ContinuousClock.Instant
    @ObservationIgnored private var artistTask: Task<Void, Never>?
    @ObservationIgnored private var albumTask: Task<Void, Never>?
    @ObservationIgnored private var navigateTask: Task<Void, Never>?

    init(connection: ServerConnection, clock: @escaping () -> ContinuousClock.Instant = { .now }) {
        self.connection = connection
        self.clock = clock
    }

    // MARK: - Selection (user actions in the columns)

    func select(artist id: String?) {
        guard id != location.artistID else { return }
        navigateTask?.cancel()
        go(to: LibraryLocation(artistID: id), column: .artist)
    }

    func select(album: AlbumSelection?) {
        guard album != location.album else { return }
        navigateTask?.cancel()
        go(to: LibraryLocation(artistID: location.artistID, album: album), column: .album)
    }

    /// Selecting a track changes the current history entry, it does not add one.
    func select(track id: String?) {
        guard id != location.trackID else { return }
        var location = location
        location.trackID = id
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

    /// Shows `entity` in the columns: resolves the artist → album → track path to it,
    /// selects that path and records it in the history. One request per hop the entity
    /// itself does not answer (a track: its album for the artist; a recording: its
    /// tracks with their albums).
    func navigate(to entity: LibraryEntity) {
        navigateTask?.cancel()
        navigateTask = Task {
            do {
                let target = try await resolve(entity)
                guard !Task.isCancelled else { return }
                go(to: target, column: .jump)
            } catch {
                guard !ServerConnection.isCancellation(error) else { return }
                connection.reportFailure(error)
            }
        }
    }

    func resolve(_ entity: LibraryEntity) async throws -> LibraryLocation {
        let client = connection.client
        let current = location.artistID
        switch entity {
        case .artist(let id):
            return LibraryLocation(artistID: id)
        case .releaseGroup(let id):
            let group = try await client.getReleaseGroup(path: .init(id: id)).ok.body.json
            return LibraryLocation(artistID: group.artistCredit.artistID(preferring: current), album: .releaseGroup(id))
        case .track(let id):
            let track = try await client.getTrack(path: .init(id: id)).ok.body.json
            let group = try await client.getReleaseGroup(path: .init(id: track.releaseGroupId)).ok.body.json
            return LibraryLocation(
                artistID: group.artistCredit.artistID(preferring: current),
                album: .releaseGroup(group.id),
                trackID: track.id
            )
        case .recording(let id):
            let recording = try await client.getRecording(path: .init(id: id)).ok.body.json
            // On an album: the track on the album already shown, else the first.
            let currentAlbum = location.album?.releaseGroupID
            if let track = recording.tracks.first(where: { $0.releaseGroup.id == currentAlbum })
                ?? recording.tracks.first
            {
                return LibraryLocation(
                    artistID: track.releaseGroup.artistCredit.artistID(preferring: current),
                    album: .releaseGroup(track.releaseGroup.id),
                    trackID: track.id
                )
            }
            return LibraryLocation(
                artistID: recording.artistCredit.artistID(preferring: current),
                album: .looseTracks,
                trackID: recording.id
            )
        }
    }

    // MARK: - Loading

    /// Reloads what is shown (window activation, reconnect); content stays meanwhile.
    func reload() {
        loadArtist()
        loadAlbum()
    }

    /// Waits for the loads in flight (tests).
    func settle() async {
        await navigateTask?.value
        await artistTask?.value
        await albumTask?.value
    }

    /// Plays a track (double-click, Return) or the album (Play album): the one place the
    /// player hooks in (stage 4).
    func play(_ request: PlayRequest) {
        onPlay?(request)
    }

    private func go(to target: LibraryLocation, column: NavigationHistory.Column) {
        let before = location
        history.push(target, column: column, at: clock())
        show(from: before)
    }

    /// Loads what changed between `before` and the current location.
    private func show(from before: LibraryLocation) {
        if location.artistID != before.artistID {
            artist = nil
            releaseGroups = []
            loadArtist()
        }
        if location.album != before.album || location.artistID != before.artistID {
            releaseGroup = nil
            tracks = []
            loadAlbum()
        }
    }

    private func loadArtist() {
        artistTask?.cancel()
        guard let id = location.artistID else {
            releaseGroupsPhase = .idle
            return
        }
        releaseGroupsPhase = .loading
        let client = connection.client
        artistTask = Task {
            do {
                // An artist's release groups fit one request (the API's page limit, 1000).
                async let detail = client.getArtist(path: .init(id: id)).ok.body.json
                async let groups = client.listArtistReleaseGroups(path: .init(id: id), query: .init(limit: 1000))
                    .ok.body.json
                let (artist, releaseGroups) = try await (detail, groups)
                guard !Task.isCancelled, id == location.artistID else { return }
                self.artist = artist
                self.releaseGroups = releaseGroups
                releaseGroupsPhase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                releaseGroupsPhase = .failed(String(localized: "Couldn’t load albums."))
                connection.reportFailure(error)
            }
        }
    }

    private func loadAlbum() {
        albumTask?.cancel()
        guard let artistID = location.artistID, let album = location.album else {
            tracksPhase = .idle
            return
        }
        tracksPhase = .loading
        let client = connection.client
        albumTask = Task {
            do {
                switch album {
                case .releaseGroup(let id):
                    async let detail = client.getReleaseGroup(path: .init(id: id)).ok.body.json
                    async let tracks = client.listReleaseGroupTracks(path: .init(id: id), query: .init(limit: 1000))
                        .ok.body.json
                    let (group, list) = try await (detail, tracks)
                    guard !Task.isCancelled, album == location.album else { return }
                    releaseGroup = group
                    self.tracks = list.map { TrackRow(track: $0, albumCreditID: group.artistCredit.id) }
                case .looseTracks:
                    let list = try await client.listArtistRecordings(
                        path: .init(id: artistID), query: .init(limit: 1000, standalone: true)
                    ).ok.body.json
                    guard !Task.isCancelled, album == location.album, artistID == location.artistID else { return }
                    releaseGroup = nil
                    tracks = list.map { TrackRow(looseRecording: $0, artistID: artistID) }
                }
                tracksPhase = .loaded
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                tracksPhase = .failed(String(localized: "Couldn’t load tracks."))
                connection.reportFailure(error)
            }
        }
    }
}
