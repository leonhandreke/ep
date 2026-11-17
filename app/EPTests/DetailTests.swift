import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

/// JSON for the detail-panel reads.
nonisolated enum DetailFixtures {
    static let stamp = LibraryFixtures.stamp

    static func entity(_ type: String, _ id: String, _ title: String, subtitle: String? = nil) -> String {
        let subtitle = subtitle.map { "\"\($0)\"" } ?? "null"
        return
            #"{"type": "\#(type)", "id": "\#(id)", "mbid": null, "title": "\#(title)", "subtitle": \#(subtitle), "cover_url": null, "release_group_type": null, "date": null, "length_ms": null, "release_group_title": null, "link_ref": "\#(id)", "link": "[[\#(type):\#(id)|\#(title)]]"}"#
    }

    static func note(_ type: String, _ id: String, body: String = "", updatedAt: String? = nil, links: String = "")
        -> String
    {
        let updated = updatedAt.map { "\"\($0)\"" } ?? "null"
        let body = String(data: try! JSONEncoder().encode(body), encoding: .utf8)!
        return
            #"{"entity_type": "\#(type)", "entity_id": "\#(id)", "body": \#(body), "updated_at": \#(updated), "links": [\#(links)]}"#
    }

    static func tag(_ id: String, _ name: String, count: Int) -> String {
        #"{"id": "\#(id)", "name": "\#(name)", "count": \#(count)}"#
    }

    static func appearance(estimated: Bool, startMS: Int?) -> String {
        let show =
            #"{"id": "s1", \#(stamp), "station": "nts", "slug": "late", "name": "Late Night Tapes", "description": "", "image_url": null, "link": "l"}"#
        let episode =
            #"{"id": "e1", \#(stamp), "show_id": "s1", "slug": "e", "name": "Homage", "description": "", "broadcast_at": "2025-03-14T22:00:00.000Z", "location": null, "url": "https://x", "source_id": null, "image_url": null, "link": "l"}"#
        let recording = LibraryFixtures.recording("r1", "Jóga", credit: LibraryFixtures.credit("c1", "Björk", artists: ["a1"]))
        let start = startMS.map(String.init) ?? "null"
        let playback =
            startMS == nil
            ? "null"
            : #"{"source_id": "x", "file_source_id": "f", "start_ms": \#(start), "end_ms": null, "file_duration_ms": null, "media_type": "audio/ogg", "codec": "opus"}"#
        return
            #"{"track_id": "rt1", "position": 7, "start_ms": \#(start), "end_ms": null, "offset_is_estimate": \#(estimated), "recording": \#(recording), "episode": \#(episode), "show": \#(show), "playback": \#(playback)}"#
    }

    /// Björk's details: a note, one tag, a backlink with a snippet whose link comes after
    /// a non-BMP character, and an estimated radio appearance.
    static let artistDetails = """
        {"entity": \(entity("artist", "a1", "Björk")),
         "note": \(note("artist", "a1", body: "Hello", updatedAt: "2026-11-01T12:00:00.000Z")),
         "tags": [\(tag("t1", "icelandic", count: 2))],
         "backlinks": [{"source": \(entity("release_group", "g1", "Homogenic")), "note_updated_at": "2026-11-01T12:00:00.000Z",
                        "snippets": [{"text": "🎻 with Björk here", "highlights": [{"start": 8, "end": 13}]}]}],
         "radio_appearances": [\(appearance(estimated: true, startMS: 4_360_000))]}
        """
}

private typealias D = DetailFixtures

func stubConnection(_ transport: StubTransport) async -> ServerConnection {
    let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: transport)
    await connection.connect()
    return connection
}

/// A transport answering by method and path (without the query); 404 otherwise.
nonisolated func methodRouted(_ routes: [String: (HTTPResponse.Status, String)]) -> StubTransport {
    StubTransport { request in
        let path = request.path?.split(separator: "?").first.map(String.init) ?? ""
        let key = "\(request.method.rawValue) \(path)"
        guard let (status, json) = routes[key] ?? (request.method == .get ? routes[path] : nil) else {
            return (HTTPResponse(status: .notFound, headerFields: [.contentType: "application/json"]), Data("{}".utf8))
        }
        var fields: HTTPFields = [.contentType: "application/json"]
        if let list = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [Any] {
            fields[HTTPField.Name("X-Total-Count")!] = String(list.count)
        }
        return (HTTPResponse(status: status, headerFields: fields), Data(json.utf8))
    }
}

@Suite(.timeLimit(.minutes(1)))
struct EntityDetailsTests {
    @Test func loadsNoteTagsBacklinksAndRadioAppearancesInOneRequest() async throws {
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server), "/entity-details/artist/a1": (.ok, D.artistDetails),
        ])
        let details = EntityDetails(connection: await stubConnection(transport))
        details.show(EntityRef(type: .artist, id: "a1"))
        #expect(details.phase == .loading)
        await details.settle()

        #expect(details.phase == .loaded)
        #expect(details.note?.text == "Hello")
        #expect(details.tags.map(\.name) == ["icelandic"])
        let backlink = try #require(details.backlinks.first)
        #expect(backlink.kind == "Album" && backlink.title == "Homogenic")
        // The highlight is in UTF-16 units: the violin counts 2.
        let snippet = try #require(backlink.snippets.first)
        #expect((snippet.text as NSString).substring(with: try #require(snippet.highlights.first)) == "Björk")
        let radio = try #require(details.radioAppearances.first)
        #expect(radio.position == "~1:12:40" && radio.isEstimated && radio.canPlay)
        #expect(radio.heading.hasPrefix("Late Night Tapes — "))
        #expect(transport.requests.count == 2)
    }

    @Test func showingNothingClearsAndAFailureIsShown() async throws {
        let transport = methodRouted(["/server": (.ok, Fixtures.server)])
        let details = EntityDetails(connection: await stubConnection(transport))
        details.show(EntityRef(type: .artist, id: "missing"))
        await details.settle()
        if case .failed = details.phase {} else { Issue.record("expected failed, got \(details.phase)") }
        details.show(nil)
        #expect(details.phase == .idle && details.note == nil)
    }

    @Test func addingAndRemovingATagShowTheServersList() async throws {
        let strings = D.tag("t2", "strings", count: 43)
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server),
            "/entity-details/release_group/g1": (
                .ok,
                #"{"entity": \#(D.entity("release_group", "g1", "Homogenic")), "note": \#(D.note("release_group", "g1")), "tags": [], "backlinks": [], "radio_appearances": []}"#
            ),
            "POST /entity-tags/release_group/g1": (.ok, "[\(strings)]"),
            "DELETE /entity-tags/release_group/g1/t2": (.ok, "[]"),
        ])
        let details = EntityDetails(connection: await stubConnection(transport))
        details.show(EntityRef(type: .releaseGroup, id: "g1"))
        await details.settle()

        details.addTag("  strings ")
        await details.settle()
        #expect(details.tags.map(\.count) == [43])
        let post = try #require(transport.requests.first { $0.request.method == .post })
        let sent = try JSONSerialization.jsonObject(with: post.body ?? Data()) as? [String: String]
        #expect(sent == ["name": "strings"])

        details.removeTag(try #require(details.tags.first))
        await details.settle()
        #expect(details.tags.isEmpty)
        #expect(details.tagError == nil)
        // The removal's answer is the list: nothing is read again.
        let reads = transport.requests.filter { $0.request.path?.hasPrefix("/entity-tags") == true }
        #expect(reads.allSatisfy { $0.request.method != .get })
    }
}

@Suite(.timeLimit(.minutes(1)))
struct TagSuggestionsTests {
    @Test func suggestsOtherTagsThenCreateUnlessTheNameExists() async throws {
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server),
            "/tags": (.ok, "[\(D.tag("t1", "string-quartet", count: 6)), \(D.tag("t2", "strings", count: 42))]"),
        ])
        let model = TagSuggestions(connection: await stubConnection(transport), debounce: .zero)
        let has = try JSONDecoder().decode(Components.Schemas.TagRead.self, from: Data(D.tag("t1", "String-Quartet", count: 6).utf8))

        model.update("str", existing: [has])
        await model.settle()
        try #require(model.tags.count == 2, "\(transport.requests.map(\.request.path))")
        #expect(model.rows == [.existing(model.tags[1]), .create("str")])
        #expect(model.chosenName == "strings")
        model.move(1)
        #expect(model.chosenName == "str")
        model.move(5)
        #expect(model.selection == 1)
        let path = try #require(transport.requests.last?.request.path)
        #expect(path.contains("prefix=str") && path.contains("sort=count"))

        model.update("Strings", existing: [])
        await model.settle()
        #expect(!model.rows.contains(.create("Strings")))  // "strings" exists, ignoring case
    }
}
