import EPAPI
import Foundation
import Testing

@testable import EP

/// An engine that plays nothing: records loads and seeks; the test sets its time and
/// ends files.
final class StubEngine: PlaybackEngine {
    var loads: [(url: URL, mediaType: String, seconds: Double)] = []
    var seeks: [Double] = []
    var playing = false
    var currentTime: Double = 0
    var volume: Float = 1
    var onEnd: (() -> Void)?
    var onFailure: ((String) -> Void)?
    var loadError: (any Error)?

    func load(_ url: URL, mediaType: String, at seconds: Double) async throws {
        loads.append((url, mediaType, seconds))
        currentTime = seconds
        if let loadError { throw loadError }
    }
    func play() { playing = true }
    func pause() { playing = false }
    func seek(to seconds: Double) {
        seeks.append(seconds)
        currentTime = seconds
    }
    func stop() { playing = false }

    /// The file ID in the last stream URL (`/sources/{id}/stream`).
    var loadedFile: String? { loads.last.map { $0.url.deletingLastPathComponent().lastPathComponent } }
}

/// JSON for playback fixtures.
nonisolated enum PlayerFixtures {
    typealias F = LibraryFixtures

    static func playback(
        file: String, start: Int = 0, end: Int? = nil, duration: Int? = 4000, mediaType: String = "audio/flac"
    ) -> String {
        #"{"source_id": "s-\#(file)-\#(start)", "file_source_id": "\#(file)", "start_ms": \#(start), "end_ms": \#(end.map(String.init) ?? "null"), "file_duration_ms": \#(duration.map(String.init) ?? "null"), "media_type": "\#(mediaType)", "codec": null}"#
    }

    static func playability(_ state: String, playback: String? = nil, episode: String? = nil, link: String? = nil)
        -> String
    {
        let radio = episode.map { #"{"track_id": "e-\#($0)", "episode_id": "\#($0)"}"# } ?? "null"
        let link = link.map { #"{"source_id": "l", "kind": "bandcamp", "url": "\#($0)"}"# } ?? "null"
        return
            #"{"state": "\#(state)", "playback": \#(playback ?? "null"), "radio_entry": \#(radio), "link": \#(link), "missing_file_source_id": null}"#
    }

    static func track(_ id: String, _ title: String, position: Int, playability: String) -> String {
        let credit = F.credit("c-a1", "Björk", artists: ["a1"])
        return
            #"{"id": "\#(id)", \#(F.stamp), "release_group_id": "g1", "disc": 1, "position": \#(position), "title": null, "recording": \#(F.recording("r-" + id, title, credit: credit)), "playability": \#(playability)}"#
    }

    /// Homogenic-like: own file, missing file, link only, own file, radio excerpt.
    static let album = [
        track("t1", "Hunter", position: 1, playability: playability("file", playback: playback(file: "f1"))),
        track("t2", "Unravel", position: 2, playability: playability("file_missing")),
        track(
            "t3", "Alarm Call", position: 3,
            playability: playability("link_only", link: "https://bjork.bandcamp.com/track/alarm-call")),
        track("t4", "Jóga", position: 4, playability: playability("file", playback: playback(file: "f4"))),
        track(
            "t5", "Cocoon", position: 5,
            playability: playability(
                "radio_excerpt", playback: playback(file: "ep1", start: 5000, end: 15000, duration: 60000),
                episode: "ep1")),
    ]

    /// Medúlla-like: three ranges of one 12 s file.
    static let singleFile = (1...3).map { n in
        track(
            "m\(n)", ["Pleasure", "Forgiveness", "Where Is the Line"][n - 1], position: n,
            playability: playability(
                "slice",
                playback: playback(
                    file: "whole", start: (n - 1) * 4000, end: n < 3 ? n * 4000 : nil, duration: 12000)))
    }

    static func entry(_ position: Int, start: Int?, end: Int?, estimated: Bool = false, recording: Bool = true)
        -> String
    {
        let credit = F.credit("c-x", "Artist \(position)", artists: ["x\(position)"])
        let rec = recording ? F.recording("rec\(position)", "Song \(position)", credit: credit) : "null"
        return
            #"{"id": "e\#(position)", \#(F.stamp), "episode_id": "ep1", "position": \#(position), "artist_text": "Raw \#(position)", "title_text": "Raw title \#(position)", "start_ms": \#(start.map(String.init) ?? "null"), "end_ms": \#(end.map(String.init) ?? "null"), "offset_is_estimate": \#(estimated), "source_id": null, "recording": \#(rec), "in_library": \#(recording), "library_playback": \#(position == 1 ? playback(file: "lib1") : "null"), "playback": null, "state": "\#(recording ? "in_library" : "not_matched")"}"#
    }

    static let episode = """
        {"id": "ep1", \(F.stamp), "show_id": "show1", "slug": "s", "name": "Homage to the night shift",
         "description": "", "broadcast_at": "2025-03-14T22:00:00.000Z", "location": "London", "url": "https://nts",
         "source_id": "epfile", "show": {"id": "show1", \(F.stamp), "station": "nts", "slug": "lnt",
           "name": "Late Night Tapes", "description": "", "image_url": null, "link": "l"},
         "playback": \(playback(file: "ep1", start: 0, end: nil, duration: 60000, mediaType: "audio/ogg")), "duration_ms": 60000,
         "tracks": [\(entry(1, start: 5000, end: 15000)), \(entry(2, start: 15000, end: 25000, estimated: true)),
                    \(entry(3, start: 30000, end: nil, recording: false)), \(entry(4, start: nil, end: nil))],
         "image_url": "/covers/radio-episode/ep1", "link": "l"}
        """
}

private typealias P = PlayerFixtures

@MainActor
private func makePlayer(routes: [String: String] = [:]) -> (Player, StubEngine) {
    let engine = StubEngine()
    let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: routed(routes))
    // A long tick interval: tests drive time with `tick()`.
    return (Player(connection: connection, engine: engine, tickInterval: .seconds(3600)), engine)
}

private func rows(_ tracks: [String]) async throws -> [TrackRow] {
    let client = Client.ep(
        serverURL: URL(string: "http://ep.test")!,
        transport: routed(["/release-groups/x/tracks": "[\(tracks.joined(separator: ","))]"]))
    let list = try await client.listReleaseGroupTracks(path: .init(id: "x"), query: .init()).ok.body.json
    return list.map { TrackRow(track: $0, albumCreditID: "c-a1") }
}

private func episode() async throws -> Components.Schemas.RadioEpisodeDetail {
    let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: StubTransport(json: P.episode))
    return try await client.getRadioEpisode(path: .init(id: "ep1")).ok.body.json
}

/// Lets the player's load task finish.
@MainActor
private func settle() async {
    for _ in 0..<20 { await Task.yield() }
}

@MainActor
struct PlayerTests {
    @Test func playingATrackQueuesTheRestOfTheAlbumThatPlays() async throws {
        let (player, engine) = makePlayer()
        let album = try await rows(P.album)

        player.play(.track(album[0], in: album, album: nil))
        await settle()
        #expect(engine.loads.first?.url.absoluteString == "http://ep.test/sources/f1/stream")
        #expect(player.isPlaying && engine.playing)
        #expect(player.title == "Hunter")
        // Missing file and link only are left out; the excerpt is queued.
        #expect(player.queue.map(\.title) == ["Jóga", "Cocoon"])
        #expect(player.libraryTarget == .track("t1"))
        #expect(player.isPlaying(album[0]) && !player.isPlaying(album[3]))

        // The file ends: the next one plays.
        engine.onEnd?()
        await settle()
        #expect(engine.loadedFile == "f4")
        #expect(player.queue.map(\.title) == ["Cocoon"])

        // Queue: remove and clear.
        player.removeFromQueue(player.queue[0].id)
        #expect(player.queue.isEmpty)

        // At the end of the queue the last item stays, paused at its start.
        engine.currentTime = 3.5
        player.tick()
        engine.onEnd?()
        #expect(!player.isPlaying)
        #expect(player.positionMS == 0)
        #expect(player.current?.title == "Jóga")
    }

    @Test func aRadioExcerptPlaysItsRangeOnTheEpisodesScrubber() async throws {
        let (player, engine) = makePlayer(routes: ["/radio/episodes/ep1": P.episode])
        let album = try await rows(P.album)

        player.play(.track(album[4], in: album, album: nil))
        await settle()
        #expect(engine.loads.last?.seconds == 5)
        #expect(player.current?.isEpisode == true)
        // Once the episode is there: its scrubber, marks and context line.
        for _ in 0..<50 where player.context == nil { await Task.yield() }
        #expect(player.scrubberRange == 0...60000)
        #expect(player.context?.hasPrefix("Late Night Tapes · ") == true)
        #expect(player.context?.hasSuffix(" · 1 of 4") == true)
        #expect(player.title == "Song 1")
        #expect(player.subtitleNote == "also in library as a file")

        // The excerpt's end ends the item, though the episode goes on.
        engine.currentTime = 15.0
        player.tick()
        #expect(!player.isPlaying)
        #expect(player.positionMS == 5000)
    }

    @Test func aSingleFileAlbumIsOneItemWhoseTitleFollowsTheTrack() async throws {
        let (player, engine) = makePlayer()
        let album = try await rows(P.singleFile)

        player.play(.track(album[1], in: album, album: nil))
        await settle()
        #expect(engine.loads.count == 1)
        #expect(engine.loads[0].seconds == 4)
        #expect(player.queue.isEmpty)
        #expect(player.scrubberRange == 0...12000)
        #expect(player.context == "Track 2 of 3 · one file")
        #expect(player.title == "Forgiveness")

        // Playing on into the next range: the title follows; no new load.
        engine.currentTime = 8.2
        player.tick()
        #expect(player.title == "Where Is the Line")
        #expect(player.libraryTarget == .track("m3"))
        #expect(engine.loads.count == 1)

        // Previous early in a track goes to the one before; later, to its start.
        engine.currentTime = 9.0
        player.tick()
        player.previous()
        #expect(engine.seeks.last == 4)
        engine.currentTime = 7.5
        player.tick()
        player.previous()
        #expect(engine.seeks.last == 4)
        // Next jumps to the next track's start.
        player.next()
        #expect(engine.seeks.last == 8)
    }

    @Test func tracksThatDoNotPlaySayWhy() async throws {
        let (player, engine) = makePlayer()
        let album = try await rows(P.album)
        player.play(.track(album[0], in: album, album: nil))
        await settle()

        player.play(.track(album[2], in: album, album: nil))
        #expect(
            player.notice
                == .linkOnly(
                    title: "Alarm Call", kind: .bandcamp,
                    url: URL(string: "https://bjork.bandcamp.com/track/alarm-call")!))
        #expect(player.current == nil && player.queue.isEmpty && !engine.playing)

        player.play(.track(album[1], in: album, album: nil))
        #expect(player.notice == .fileMissing(title: "Unravel"))
        #expect(engine.loads.count == 1)

        // Playing something clears the notice.
        player.play(.track(album[3], in: album, album: nil))
        await settle()
        #expect(player.notice == nil && player.title == "Jóga")
    }

    @Test func aFailedLoadShowsWhy() async throws {
        let (player, engine) = makePlayer()
        engine.loadError = HTTPRangeSource.Failure.status(404)
        let album = try await rows(P.album)
        player.play(.track(album[0], in: album, album: nil))
        await settle()
        #expect(player.notice == .failed(title: "Hunter", message: "The server has no such file."))
        #expect(!player.isPlaying)
    }

    @Test func playAlbumLoadsItsTracks() async throws {
        let (player, engine) = makePlayer(routes: [
            "/release-groups/g1": LibraryFixtures.releaseGroupDetail("g1", "Homogenic", artist: "a1"),
            "/release-groups/g1/tracks": "[\(P.album.joined(separator: ","))]",
        ])
        player.play(.album("g1"))
        for _ in 0..<100 where engine.loads.isEmpty { await Task.yield() }
        #expect(engine.loadedFile == "f1")
        #expect(player.subtitle == "Björk — Homogenic")
        #expect(player.queue.count == 2)
    }
}

@MainActor
struct EpisodePlaybackTests {
    @Test func anEpisodePlaysFromAnEntryWithItsTracklistAsMarks() async throws {
        let (player, engine) = makePlayer()
        player.playEpisode(try await episode(), from: "e2")
        await settle()

        #expect(engine.loads.last?.url.absoluteString == "http://ep.test/sources/ep1/stream")
        #expect(engine.loads.last?.seconds == 15)
        #expect(engine.loads.last?.mediaType == "audio/ogg")  // the engine's choice
        let item = try #require(player.current)
        // Untimed entries get no mark; estimated ones are marked as such.
        #expect(item.entries.map(\.id) == ["e1", "e2", "e3"])
        #expect(item.entries.map(\.isEstimated) == [false, true, false])
        #expect(player.title == "Song 2")
        #expect(player.context?.hasSuffix(" · 2 of 4") == true)
        #expect(player.libraryTarget == .recording("rec2"))

        // A gap between entries: the episode's own title; nothing to navigate to.
        engine.currentTime = 27
        player.tick()
        #expect(player.title == "Homage to the night shift")
        #expect(player.subtitle == "Late Night Tapes")
        #expect(player.libraryTarget == nil)

        // An unmatched entry shows the station's text and leads nowhere.
        engine.currentTime = 31
        player.tick()
        #expect(player.title == "Raw title 3")
        #expect(player.libraryTarget == nil)

        // Previous early in an entry: the entry before, then the one before that.
        player.previous()
        #expect(engine.seeks.last == 15)
        player.previous()
        #expect(engine.seeks.last == 5)
        #expect(player.scrubberRange == 0...60000)
        #expect(player.remainingMS == 55000)
    }

    @Test func anEpisodeWithoutAudioDoesNotPlay() async throws {
        let silent = P.episode.replacingOccurrences(
            of:
                #""playback": \#(P.playback(file: "ep1", start: 0, end: nil, duration: 60000, mediaType: "audio/ogg"))"#,
            with: #""playback": null"#)
        let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: StubTransport(json: silent))
        let detail = try await client.getRadioEpisode(path: .init(id: "ep1")).ok.body.json
        let (player, engine) = makePlayer()
        player.playEpisode(detail)
        #expect(player.notice == .noSource(title: "Homage to the night shift"))
        #expect(engine.loads.isEmpty)
    }

    @Test func entryLookupRespectsGapsAndTheSpan() {
        let entries = [
            PlayItem.Entry(id: "a", startMS: 0, endMS: 4000, title: "A", artist: "", number: 1),
            PlayItem.Entry(id: "b", startMS: 4000, endMS: 8000, title: "B", artist: "", number: 2),
            PlayItem.Entry(id: "c", startMS: 10000, endMS: nil, title: "C", artist: "", number: 3),
        ]
        let item = PlayItem(
            kind: .singleFile(album: "X", trackCount: 3), title: "X", subtitle: "", fileSourceID: "f",
            mediaType: "audio/flac", durationMS: 12000, startMS: 0, endMS: 9000, playFromMS: 0, entries: entries)
        #expect(item.entry(at: 3999)?.id == "a")
        #expect(item.entry(at: 4000)?.id == "b")
        #expect(item.entry(at: 9000) == nil)  // a gap
        #expect(item.entry(at: 11000)?.id == "c")
        #expect(item.entry(after: 1000)?.id == "b")
        #expect(item.entry(after: 4100) == nil)  // c starts after the span's end
        #expect(item.entry(before: 4000)?.id == "a")
    }
}
