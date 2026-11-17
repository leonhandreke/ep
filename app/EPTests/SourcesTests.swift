import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

nonisolated enum SourcesFixtures {
    static func source(_ id: String, kind: String, uri: String?, start: Int? = nil, end: Int? = nil) -> String {
        let uri = uri.map { "\"\($0)\"" } ?? "null"
        return
            #"{"id": "\#(id)", "created_at": "2026-01-03T12:00:00.000Z", "updated_at": "2026-01-03T12:00:00.000Z", "kind": "\#(kind)", "uri": \#(uri), "parent_id": null, "start_ms": \#(start.map(String.init) ?? "null"), "end_ms": \#(end.map(String.init) ?? "null"), "recording_id": "r1", "managed": true, "media_type": null, "codec": null, "duration_ms": null, "size": null, "sha256": null, "sample_rate": null, "bit_depth": null, "bit_rate": null, "channels": null, "host_path": null, "playback": null}"#
    }

    static func file(_ id: String, exists: Bool = true) -> String {
        #"{"source_id": "\#(id)", "path": "/library/Björk/Homogenic/02 Jóga.flac", "host_path": "/Users/x/Music/EP/Björk/Homogenic/02 Jóga.flac", "exists": \#(exists), "media_type": "audio/flac", "codec": "flac", "sample_rate": 44100, "bit_depth": 16, "bit_rate": null, "duration_ms": 4000}"#
    }

    /// The sources of r1: its file f1, a missing file f2 and a link; `preferred` is
    /// chosen, `willPlay` will play.
    static func list(preferred: String? = nil, willPlay: String = "f1") -> String {
        func flags(_ id: String) -> String {
            #""preferred": \#(id == preferred), "will_play": \#(id == willPlay)"#
        }
        return """
            [{"source": \(source("f1", kind: "file", uri: "/library/Björk/Homogenic/02 Jóga.flac")), "role": "file",
              "ancestors": [\(source("bc", kind: "bandcamp", uri: "https://bjork.bandcamp.com/track/joga"))],
              "file": \(file("f1")), "radio": null, "playable": true, \(flags("f1"))},
             {"source": \(source("f2", kind: "file", uri: "/watch/old.mp3")), "role": "file", "ancestors": [],
              "file": \(file("f2", exists: false)), "radio": null, "playable": true, \(flags("f2"))},
             {"source": \(source("yt", kind: "youtube", uri: "https://youtube.com/watch?v=x")), "role": "link",
              "ancestors": [], "file": null, "radio": null, "playable": false, \(flags("yt"))}]
            """
    }
}

private typealias SF = SourcesFixtures

struct SourcesStoreTests {
    private func store(_ extra: [String: (HTTPResponse.Status, String)] = [:]) async -> (SourcesStore, StubTransport) {
        var routes: [String: (HTTPResponse.Status, String)] = [
            "/server": (.ok, Fixtures.server),
            "/recordings/r1/sources": (.ok, SF.list()),
        ]
        routes.merge(extra) { $1 }
        let transport = methodRouted(routes)
        return (
            SourcesStore(recordingID: "r1", connection: await stubConnection(transport)),
            transport
        )
    }

    private func bodies(_ transport: StubTransport, _ method: HTTPRequest.Method) throws -> [NSDictionary?] {
        try transport.requests.filter { $0.request.method == method }.map {
            try JSONSerialization.jsonObject(with: #require($0.body)) as? NSDictionary
        }
    }

    @Test func listsTheSourcesWithWhatEachShows() async throws {
        let (store, _) = await store()
        store.load()
        await store.settle()
        #expect(store.sources.count == 3)
        #expect(store.chosenID == "f1" && !store.hasPreference)
        let file = store.sources[0]
        #expect(file.codecChip == "FLAC 16/44.1")
        #expect(file.provenance.hasPrefix("from Bandcamp · "))
        #expect(file.location == "/Users/x/Music/EP/Björk/Homogenic/02 Jóga.flac")
        #expect(store.sources[1].isMissing)
        #expect(store.sources[2].title == "Link" && store.sources[2].provenance == "YouTube")
    }

    @Test func choosingAndGoingBackToTheDefaultShowTheAnswer() async throws {
        let (store, transport) = await store([
            "PUT /recordings/r1/preferred-source": (.ok, SF.list(preferred: "f1"))
        ])
        var changes = 0
        store.onChange = { changes += 1 }
        store.choose("f1")
        await store.settle()
        #expect(store.chosenID == "f1" && store.hasPreference)
        store.choose(nil)
        await store.settle()
        #expect(try bodies(transport, .put) == [["source_id": "f1"], [:]])
        #expect(changes == 2)
        #expect(store.error == nil)
        // Each answer is the list: nothing is read again.
        #expect(transport.requests.filter { $0.request.path != "/server" }.allSatisfy { $0.request.method == .put })
    }

    @Test func theOneThatWillPlayIsChosenWithoutAPreference() async throws {
        let (store, _) = await store(["/recordings/r1/sources": (.ok, SF.list(willPlay: "f2"))])
        store.load()
        await store.settle()
        #expect(store.chosenID == "f2" && !store.hasPreference)
    }

    @Test func locateMovesTheFileAndAddingALinkAsksWhatItIs() async throws {
        let (store, transport) = await store([
            "PATCH /recordings/r1/sources/f2": (.ok, SF.list(willPlay: "f2")),
            "/links/classify": (
                .ok,
                #"{"link_kind": "bandcamp_track", "source_kind": "bandcamp", "import_kind": "bandcamp", "url": "https://bjork.bandcamp.com/track/joga"}"#
            ),
            "POST /recordings/r1/sources": (.created, SF.list()),
        ])
        store.locate("f2", at: "/Users/x/Music/found.mp3")
        await store.settle()
        store.addLink("  https://bjork.bandcamp.com/track/joga?from=x ")
        await store.settle()
        #expect(try bodies(transport, .patch) == [["uri": "/Users/x/Music/found.mp3"]])
        #expect(try bodies(transport, .post) == [["kind": "bandcamp", "uri": "https://bjork.bandcamp.com/track/joga"]])
        #expect(store.error == nil)
    }

    @Test func aLinkEPDoesNotKnowIsNotAdded() async throws {
        let (store, transport) = await store([
            "/links/classify": (.ok, #"{"link_kind": null, "source_kind": null, "import_kind": null, "url": null}"#)
        ])
        store.addLink("https://example.com/song")
        await store.settle()
        #expect(store.error == "EP doesn’t know this kind of link.")
        #expect(transport.requests.allSatisfy { $0.request.method != .post })
    }
}
