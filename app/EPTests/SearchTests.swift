import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

nonisolated enum SearchFixtures {
    static func hit(_ type: String, _ id: String, _ title: String, radioMatch: String = "null") -> String {
        #"{"type": "\#(type)", "id": "\#(id)", "mbid": null, "title": "\#(title)", "subtitle": "Björk", "cover_url": null, "release_group_type": null, "date": "2024-11-24", "length_ms": null, "release_group_title": null, "link_ref": "\#(id)", "link": "[[\#(type):\#(id)|\#(title)]]", "score": 1.0, "track_count": null, "release_group_count": null, "loose_recording_count": null, "radio_match": \#(radioMatch)}"#
    }

    static let radioMatch =
        #"{"track_id": "t7", "recording": \#(DetailFixtures.entity("recording", "r3", "Cocoon")), "start_ms": 2470000, "offset_is_estimate": false}"#

    /// "bjork vesp": Vespertine first, then tracks, the artist and an episode that played Cocoon.
    static let results = "[" + [
        hit("release_group", "g1", "Vespertine"),
        hit("recording", "r1", "Hidden Place"),
        hit("artist", "a1", "Björk"),
        hit("recording", "r2", "Pagan Poetry"),
        hit("radio_episode", "e1", "Strings only", radioMatch: radioMatch),
    ].joined(separator: ",") + "]"
}

private typealias S = SearchFixtures

/// A transport for `/search` answering per query text, and with `tagged` without one.
nonisolated func searchTransport(
    delay: [String: Double] = [:], tagged: String = "[]", results: @escaping @Sendable (String) -> String
) -> StubTransport {
    StubTransport { request in
        let url = URLComponents(string: request.path ?? "")!
        if url.path == "/server" {
            return (jsonResponse(), Data(Fixtures.server.utf8))
        }
        guard let q = url.queryItems?.first(where: { $0.name == "q" })?.value else {
            return (jsonResponse(total: count(of: tagged)), Data(tagged.utf8))
        }
        if let seconds = delay[q] { Thread.sleep(forTimeInterval: seconds) }
        let json = results(q)
        return (jsonResponse(total: count(of: json)), Data(json.utf8))
    }
}

/// How many items a JSON array has (`X-Total-Count`).
private nonisolated func count(of json: String) -> Int {
    ((try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [Any])?.count ?? 0
}

@Suite(.timeLimit(.minutes(1)))
struct SearchStoreTests {
    @Test func groupsTheHitsUnderATopHit() async throws {
        let transport = searchTransport { _ in S.results }
        let store = SearchStore(connection: await stubConnection(transport), debounce: .zero)
        store.update("bjork vesp")
        await store.settle()

        try #require(store.groups.count == 4)  // a failed search must not crash the host below
        #expect(store.groups.map(\.kind) == [.topHit, .tracks, .artists, .radio])
        #expect(store.groups[1].hits.map(\.title) == ["Hidden Place", "Pagan Poetry"])
        #expect(store.hits.map(\.title) == ["Vespertine", "Hidden Place", "Pagan Poetry", "Björk", "Strings only"])
        let episode = try #require(store.hits.last)
        #expect(episode.radioEntryID == "t7")
        #expect(episode.detail.hasSuffix("plays “Cocoon” at 41:10"))

        store.move(10)
        #expect(store.selected?.title == "Strings only")
        let query = try #require(transport.requests.last?.request.path)
        #expect(query.contains("per_type=6") && query.contains("include_radio_only=true"))
    }

    @Test func typingQuicklySearchesOnceForTheLastText() async throws {
        let transport = searchTransport { _ in S.results }
        let store = SearchStore(connection: await stubConnection(transport), debounce: .milliseconds(50))
        store.update("b")
        store.update("bj")
        store.update("bjo")
        await store.settle()
        let searches = transport.requests.filter { $0.request.path?.hasPrefix("/search") == true }
        #expect(searches.count == 1)
        #expect(searches.first?.request.path?.contains("q=bjo") == true)
    }

    @Test func aSlowAnswerToAnOlderQueryIsDropped() async throws {
        let transport = searchTransport(delay: ["old": 0.3]) { q in
            q == "old" ? "[\(S.hit("artist", "a9", "Old"))]" : "[\(S.hit("artist", "a1", "New"))]"
        }
        let store = SearchStore(connection: await stubConnection(transport), debounce: .zero)
        store.update("old")
        try await Task.sleep(for: .milliseconds(50))
        store.update("new")
        await store.settle()
        try await Task.sleep(for: .milliseconds(400))
        #expect(store.hits.map(\.title) == ["New"])
    }

    @Test func aTagFilterListsEverythingTaggedThenSearchesWithin() async throws {
        let tagged = "[\(S.hit("artist", "a1", "Björk")), \(S.hit("release_group", "g1", "Homogenic"))]"
        let transport = searchTransport(tagged: tagged) { _ in "[\(S.hit("release_group", "g1", "Homogenic"))]" }
        let store = SearchStore(connection: await stubConnection(transport), debounce: .zero)
        let tag = try JSONDecoder().decode(
            Components.Schemas.TagRead.self, from: Data(DetailFixtures.tag("t1", "strings", count: 2).utf8))

        store.filter(by: tag)
        await store.settle()
        // Nothing typed: everything with the tag, grouped, no top hit.
        #expect(store.groups.map(\.kind) == [.albums, .artists])
        let path = try #require(transport.requests.last?.request.path)
        #expect(path.hasPrefix("/search?") && path.contains("tag_id=t1") && !path.contains("q="))

        store.update("homo")
        await store.settle()
        #expect(transport.requests.last?.request.path?.contains("tag_id=t1") == true)
        #expect(store.hits.map(\.title) == ["Homogenic"])

        store.update("")
        store.filter(by: nil)
        #expect(store.isEmpty && store.hits.isEmpty)
    }

    @Test func aTagsListIsReadPageByPage() async throws {
        let all = (0..<250).map { S.hit("artist", "a\($0)", "Artist \($0)") }
        let transport = StubTransport { request in
            let url = URLComponents(string: request.path ?? "")!
            if url.path == "/server" { return (jsonResponse(), Data(Fixtures.server.utf8)) }
            func number(_ name: String) -> Int {
                url.queryItems?.first { $0.name == name }?.value.flatMap(Int.init) ?? 0
            }
            let page = all.dropFirst(number("offset")).prefix(number("limit"))
            return (jsonResponse(total: all.count), Data(("[" + page.joined(separator: ",") + "]").utf8))
        }
        let store = SearchStore(connection: await stubConnection(transport), debounce: .zero)
        let tag = try JSONDecoder().decode(
            Components.Schemas.TagRead.self, from: Data(DetailFixtures.tag("t1", "ambient", count: 250).utf8))
        store.filter(by: tag)
        await store.settle()
        #expect(store.hits.count == 250)
        #expect(store.hits.last?.title == "Artist 249")
        let searches = transport.requests.filter { $0.request.path?.hasPrefix("/search") == true }
        #expect(searches.count == 3 && searches.allSatisfy { $0.request.path?.contains("limit=100") == true })
    }
}

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct SearchPlayTests {
    /// ⌥↩ in the palette: with nothing playing the album plays; then it goes in front of
    /// the queue, the current item going on.
    @Test func playNextPlaysWhenIdleAndOtherwiseQueuesInFront() async throws {
        let routes = [
            "/release-groups/g1": LibraryFixtures.releaseGroupDetail("g1", "Homogenic", artist: "a1"),
            "/release-groups/g1/tracks": "[\(PlayerFixtures.album.joined(separator: ","))]",
        ]
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: routed(routes))
        let player = Player(connection: connection, engine: StubEngine(), tickInterval: .seconds(3600))
        let album = EntityRef(type: .releaseGroup, id: "g1")

        player.play(album, next: true)
        await player.settle()
        #expect(player.current?.title == "Hunter")
        #expect(player.queue.map(\.title) == ["Jóga", "Cocoon"])

        player.play(album, next: true)
        await player.settle()
        #expect(player.current?.title == "Hunter")
        #expect(player.queue.map(\.title) == ["Hunter", "Jóga", "Cocoon", "Jóga", "Cocoon"])
        #expect(!Player.plays(.artist) && Player.plays(.radioEpisode))
    }
}
