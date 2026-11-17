import EPAPI
import Foundation
import HTTPTypes
import SwiftUI
import Synchronization
import Testing

@testable import EP

/// Stage 10: retry while unreachable, watched folders, relative job dates, the review
/// preview, the remembered volume, the selection a list shows.
@MainActor
@Suite(.timeLimit(.minutes(1)))
struct StatesTests {
    private static let folder =
        #"{"id": "w1", "created_at": "2026-11-17T20:00:00.000Z", "updated_at": "2026-11-17T20:00:00.000Z", "path": "/watch/Library", "host_path": "/Users/x/Music/Library", "enabled": true, "baseline_at": "2026-11-17T20:00:00.000Z", "status": "idle", "last_import_at": null}"#

    @Test func retriesWaitLongerUpToTheLastDelay() {
        let delays: [Duration] = [.seconds(5), .seconds(10), .seconds(30)]
        let waits = (0...5).map { ServerConnection.retryDelay(afterFailures: $0, delays: delays) }
        #expect(waits == [nil, .seconds(5), .seconds(10), .seconds(30), .seconds(30), .seconds(30)])
        #expect(ServerConnection.retryDelay(afterFailures: 1, delays: []) == nil)
    }

    @Test func anUnreachableServerIsTriedAgainUntilItAnswers() async throws {
        let up = Mutex(false)
        let transport = StubTransport { _ in
            guard up.withLock({ $0 }) else { throw URLError(.cannotConnectToHost) }
            return (
                HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(Fixtures.server.utf8)
            )
        }
        let connection = ServerConnection(
            serverURL: URL(string: "http://ep.test")!, transport: transport, retryDelays: [.milliseconds(50)])

        await connection.connect()
        #expect(connection.isOffline && connection.retryAt != nil)
        up.withLock { $0 = true }
        for _ in 0..<100 where !connection.isConnected { try await Task.sleep(for: .milliseconds(20)) }
        #expect(connection.isConnected && !connection.isOffline && connection.retryAt == nil)
        #expect(transport.requests.count >= 2)
    }

    @Test func watchedFoldersAreAddedChangedAndRemovedOnTheServer() async throws {
        let enabled = Self.folder.replacingOccurrences(of: #""enabled": true"#, with: #""enabled": false"#)
            .replacingOccurrences(of: #""idle""#, with: #""paused""#)
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/watch-dirs": [(.ok, "[]")],
            "POST /watch-dirs": [(.created, Self.folder), (.conflict, #"{"detail": "watched already"}"#)],
            "PATCH /watch-dirs/w1": [(.ok, enabled)],
            "DELETE /watch-dirs/w1": [(.noContent, "")],
        ])
        let folders = WatchFolders(connection: await stubConnection(transport.stub))
        await folders.load()
        #expect(folders.isLoaded && folders.folders.isEmpty)

        let added = try #require(await folders.add(path: "/Users/x/Music/Library"))
        #expect(added.displayPath == "/Users/x/Music/Library" && folders.folders.count == 1)
        #expect(await folders.add(path: "/Users/x/Music/Library") == nil)
        #expect(folders.error == "This folder is watched already.")

        await folders.setEnabled(false, for: "w1")
        let paused = try #require(folders.folders.first)
        #expect(!paused.enabled && paused.statusText() == "paused")
        await folders.remove("w1")
        #expect(folders.folders.isEmpty && folders.error == nil)
        let body = try #require(transport.stub.requests.first { $0.request.method == .post }?.body)
        #expect(try JSONSerialization.jsonObject(with: body) as? NSDictionary == ["path": "/Users/x/Music/Library"])
    }

    @Test func jobTimesSayTodayAndYesterdayAsTheSystemDoes() throws {
        let now = Date.now
        let today = ImportFormat.time(now, now: now)
        #expect(today.contains(now.formatted(date: .omitted, time: .shortened)))
        let yesterday = try #require(Calendar.current.date(byAdding: .day, value: -1, to: now))
        #expect(ImportFormat.time(yesterday, now: now) != ImportFormat.time(yesterday, now: now.addingTimeInterval(86_400 * 3)))
        let older = try #require(Calendar.current.date(byAdding: .day, value: -10, to: now))
        #expect(ImportFormat.time(older, now: now) == older.formatted(.dateTime.day().month(.abbreviated)))
    }

    @Test func anIncomingTrackWithItsFileThereCanBePreviewed() async throws {
        let withMedium = ImportFixtures.radioProposal.replacingOccurrences(
            of: #"{"media": []"#,
            with:
                #"{"media": [{"key": "ep", "path": "/library/_work/ep.opus", "managed": true, "origin": null, "download": true, "media_type": "audio/ogg", "codec": "opus", "duration_ms": 7200000, "size": null, "sha256": null, "sample_rate": null, "bit_depth": null, "bit_rate": null, "channels": null}]"#
        )
        let job = try JSONDecoder.iso8601Fractional.decode(
            Components.Schemas.ImportJobRead.self,
            from: Data(ImportFixtures.read("j1", state: "needs_review", proposal: withMedium).utf8))
        let rows = ReviewModel.rows(for: job)
        let first = try #require(rows.first { $0.id == "uncertain_recording:ep#1" })
        #expect(first.preview == .init(trackKey: "ep#1", mediaType: "audio/ogg", startMS: 602_000, endMS: nil))

        let without = try JSONDecoder.iso8601Fractional.decode(
            Components.Schemas.ImportJobRead.self,
            from: Data(ImportFixtures.read("j1", state: "needs_review", proposal: ImportFixtures.radioProposal).utf8))
        #expect(ReviewModel.rows(for: without).allSatisfy { $0.preview == nil })

        let engine = StubEngine()
        let preview = ReviewPreview { engine }
        let url = ReviewPreview.url(server: URL(string: "http://ep.test")!, jobID: "j1", trackKey: "ep#1")
        #expect(url.absoluteString == "http://ep.test/imports/j1/stream?track_key=ep%231")
        preview.toggle(try #require(first.preview), rowID: first.id, url: url)
        for _ in 0..<50 where !engine.playing { try await Task.sleep(for: .milliseconds(10)) }
        #expect(engine.playing && engine.loads.first?.seconds == 602 && preview.rowID == first.id)
        preview.toggle(try #require(first.preview), rowID: first.id, url: url)
        #expect(!engine.playing && preview.rowID == nil)
    }

    @Test func theVolumeIsRememberedAcrossLaunches() throws {
        let suite = "ep.tests.volume.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: StubTransport(json: "{}"))
        Player(connection: connection, engine: StubEngine(), defaults: defaults).volume = 0.4
        let engine = StubEngine()
        let player = Player(connection: connection, engine: engine, defaults: defaults)
        #expect(player.volume == 0.4 && engine.volume == 0.4)
    }

    @Test func aListSelectionIsReadWhenAsked() {
        var selected: String? = "a"
        let binding = shownSelection(selected) { selected = $0 }
        #expect(binding.wrappedValue == "a")
        binding.wrappedValue = "b"
        #expect(selected == "b")
        #expect(binding.wrappedValue == "b")
    }

    @Test func anEmptyServerIsAnEmptyLibrary() throws {
        let decode = { (json: String) in
            try JSONDecoder().decode(Components.Schemas.LibraryCountsRead.self, from: Data(json.utf8))
        }
        let empty =
            #"{"artists": 0, "radio_only_artists": 0, "release_groups": 0, "recordings": 0, "tracks": 0, "radio_shows": 0, "radio_episodes": 0}"#
        #expect(try decode(empty).isEmptyLibrary)
        #expect(try !decode(empty.replacingOccurrences(of: #""radio_shows": 0"#, with: #""radio_shows": 1"#)).isEmptyLibrary)
    }
}
