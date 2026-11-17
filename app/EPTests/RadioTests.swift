import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

/// JSON for the radio reads.
nonisolated enum RadioFixtures {
    static let stamp = LibraryFixtures.stamp

    static func show(_ id: String, _ name: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "station": "nts", "slug": "s", "name": "\#(name)", "description": "", "image_url": null, "link": "[[radio_show:\#(id)|\#(name)]]"}"#
    }

    static func showDetail(_ id: String, _ name: String, episodes: Int) -> String {
        #"{"id": "\#(id)", \#(stamp), "station": "nts", "slug": "s", "name": "\#(name)", "description": "", "episode_count": \#(episodes), "image_url": null, "link": "l"}"#
    }

    static func episode(_ id: String, _ name: String, show: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "show_id": "\#(show)", "slug": "e", "name": "\#(name)", "description": "", "broadcast_at": "2025-03-14T22:00:00.000Z", "location": "London", "url": "https://x", "source_id": "f1", "image_url": null, "link": "l"}"#
    }

    static let playback =
        #"{"source_id": "f1", "file_source_id": "f1", "start_ms": 0, "end_ms": null, "file_duration_ms": 60000, "media_type": "audio/ogg", "codec": "opus"}"#

    static func entry(
        _ id: String, position: Int, start: Int?, recording: String? = nil, state: String = "not_matched",
        estimated: Bool = false
    ) -> String {
        let recordingJSON =
            recording.map {
                LibraryFixtures.recording($0, "Jóga", credit: LibraryFixtures.credit("c1", "Björk", artists: ["a1"]))
            } ?? "null"
        let startJSON = start.map(String.init) ?? "null"
        return
            #"{"id": "\#(id)", \#(stamp), "episode_id": "e1", "position": \#(position), "artist_text": "Björk", "title_text": "Jóga", "start_ms": \#(startJSON), "end_ms": null, "offset_is_estimate": \#(estimated), "source_id": null, "recording": \#(recordingJSON), "in_library": \#(state == "in_library"), "library_playback": null, "playback": null, "state": "\#(state)"}"#
    }

    static func episodeDetail(_ id: String, show: String, entries: [String]) -> String {
        let base = episode(id, "Homage to the night shift", show: show).dropLast()
        return
            #"\#(base), "show": \#(Self.show(show, "Late Night Tapes")), "playback": \#(playback), "duration_ms": 60000, "tracks": [\#(entries.joined(separator: ","))]}"#
    }

    static var routes: [String: (HTTPResponse.Status, String)] {
        [
            "/server": (.ok, Fixtures.server),
            "/radio/shows": (.ok, "[\(show("s1", "Late Night Tapes"))]"),
            "/radio/shows/s1": (.ok, showDetail("s1", "Late Night Tapes", episodes: 1)),
            "/radio/shows/s1/episodes": (.ok, "[\(episode("e1", "Homage to the night shift", show: "s1"))]"),
            "/radio/episodes/e1": (
                .ok,
                episodeDetail(
                    "e1", show: "s1",
                    entries: [
                        entry("t1", position: 1, start: 5000, recording: "r1", state: "in_library"),
                        entry("t2", position: 2, start: 15000, estimated: true),
                    ])
            ),
        ]
    }
}

private typealias R = RadioFixtures

@Suite(.timeLimit(.minutes(1)))
struct RadioBrowserTests {
    @Test func selectingAShowAndEpisodeLoadsTheTracklistAndHistoryGoesBack() async throws {
        let radio = RadioBrowser(connection: await stubConnection(methodRouted(R.routes)))
        radio.reload()
        await radio.settle()
        #expect(radio.shows.map(\.name) == ["Late Night Tapes"])

        radio.select(show: "s1")
        await radio.settle()
        #expect(radio.show?.episodeCount == 1)
        #expect(radio.episodes.map(\.id) == ["e1"])

        radio.select(episode: "e1")
        await radio.settle()
        try #require(radio.entries.count == 2)
        #expect(radio.entries.map(\.id) == ["t1", "t2"])
        #expect(RadioFormat.tracklistMeta(try #require(radio.episode)) == "2 entries · 1:00 · 1 matched")
        #expect(RadioFormat.time(radio.entries[1]) == "~0:15")
        #expect(RadioFormat.state(radio.entries[1]) == "not matched · est. time")
        #expect(RadioFormat.state(radio.entries[0]) == "in library, no file")

        // Selecting an entry is not a navigation; Back goes to the show.
        radio.select(entry: "t2")
        radio.goBack()
        #expect(radio.location == RadioLocation(showID: "s1"))
        #expect(radio.episode == nil)
        radio.goForward()
        await radio.settle()
        #expect(radio.location == RadioLocation(showID: "s1", episodeID: "e1", entryID: "t2"))
        #expect(radio.entries.count == 2)
    }

    @Test func navigateToAnEntryAsksTheEpisodeForItsShow() async throws {
        let radio = RadioBrowser(connection: await stubConnection(methodRouted(R.routes)))
        radio.navigate(to: OpenTarget(entity: EntityRef(type: .radioEpisode, id: "e1"), radioEntryID: "t2"))
        await radio.settle()
        #expect(radio.location == RadioLocation(showID: "s1", episodeID: "e1", entryID: "t2"))
        #expect(radio.selectedEntry?.position == 2)
        #expect(radio.episodes.count == 1)
        #expect(radio.canGoBack)

        // A Library entity is not the Radio view's business.
        #expect(try await radio.resolve(OpenTarget(entity: EntityRef(type: .artist, id: "a1"))) == nil)
    }

    @Test func playingFromAnEntryHandsTheEpisodeToThePlayer() async throws {
        let radio = RadioBrowser(connection: await stubConnection(methodRouted(R.routes)))
        var played: (String, String?)?
        radio.onPlay = { episode, entry in played = (episode.id, entry) }
        radio.navigate(to: OpenTarget(entity: EntityRef(type: .radioEpisode, id: "e1")))
        await radio.settle()
        radio.play(from: "t2")
        #expect(played?.0 == "e1" && played?.1 == "t2")
    }

    @Test func repointingSendsTheRecordingAndUnmatchingSendsAnExplicitNull() async throws {
        var routes = R.routes
        routes["PATCH /radio/episode-tracks/t2"] = (.ok, R.entry("t2", position: 2, start: 15000))
        let transport = methodRouted(routes)
        let radio = RadioBrowser(connection: await stubConnection(transport))
        radio.navigate(to: OpenTarget(entity: EntityRef(type: .radioEpisode, id: "e1")))
        await radio.settle()
        let loads = transport.requests.filter { $0.request.path == "/radio/episodes/e1" }.count

        radio.repoint(entry: "t2", to: "r9")
        await radio.settle()
        radio.repoint(entry: "t2", to: nil)
        await radio.settle()

        let patches = transport.requests.filter { $0.request.method == .patch }
        let bodies = try patches.map { try JSONSerialization.jsonObject(with: #require($0.body)) as? NSDictionary }
        #expect(bodies == [["recording_id": "r9"], ["recording_id": NSNull()]])
        // The tracklist is read again after each change.
        #expect(transport.requests.filter { $0.request.path == "/radio/episodes/e1" }.count == loads + 2)
        #expect(radio.entryError == nil)
    }

    @Test func aFailedRepointIsShown() async throws {
        let radio = RadioBrowser(connection: await stubConnection(methodRouted(R.routes)))
        radio.navigate(to: OpenTarget(entity: EntityRef(type: .radioEpisode, id: "e1")))
        await radio.settle()
        radio.repoint(entry: "t2", to: "r9")
        await radio.settle()
        #expect(radio.entryError != nil)
    }
}

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct RadioPlayingTests {
    /// An episode played from the Radio view is shown there at the entry playing; a
    /// track's radio excerpt played from the Library is shown in the Library.
    @Test func showPlayingGoesToTheEpisodeAtTheEntryPlaying() async throws {
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: routed([:]))
        let player = Player(connection: connection, engine: StubEngine(), tickInterval: .seconds(3600))
        let client = Client.ep(
            serverURL: URL(string: "http://ep.test")!, transport: StubTransport(json: PlayerFixtures.episode))
        let episode = try await client.getRadioEpisode(path: .init(id: "ep1")).ok.body.json

        player.playEpisode(episode, from: "e2")
        #expect(
            player.radioTarget == OpenTarget(entity: EntityRef(type: .radioEpisode, id: "ep1"), radioEntryID: "e2"))
        #expect(player.canShowPlaying)
    }
}
