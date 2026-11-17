import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

nonisolated private let uuid = "c100693e-b568-5ae6-9048-cada7a052a49"

private func text(_ string: String, _ range: NSRange) -> String {
    (string as NSString).substring(with: range)
}

@Suite(.timeLimit(.minutes(1)))
struct NoteLinksTests {
    @Test func parsesLinksWithUTF16RangesPastNonBMPAndCombiningCharacters() throws {
        // 🎻 is two UTF-16 units; "é" is "e" + a combining accent (two units, one character).
        let body = "🎻 Cafe\u{301} [[artist:\(uuid.uppercased())|Björk]] and [[release_group:\(uuid)]]."
        let links = NoteLinks.parse(body)
        #expect(links.count == 2)
        let first = links[0]
        #expect(first.type == .artist && first.ref == uuid && first.display == "Björk")
        #expect(first.range.location == 9)  // 🎻 2, space, "Cafe" 4, the accent 1, space
        #expect(text(body, first.range) == "[[artist:\(uuid.uppercased())|Björk]]")
        #expect(text(body, first.displayRange) == "Björk")
        #expect(text(body, first.hiddenRange) == "artist:\(uuid.uppercased())|")
        #expect(links[1].display == nil && text(body, links[1].hiddenRange) == "release_group:\(uuid)")
        #expect(links[1].displayRange.length == 0)
    }

    @Test func codeEscapesAndUnknownTypesAreNotLinks() {
        let link = "[[artist:\(uuid)|A]]"
        let body = """
            `\(link)` \\\(link) [[band:\(uuid)|x]] [[artist:not-a-uuid]]
            ```
            \(link)
            ```
            ok \(link)
            """
        let links = NoteLinks.parse(body)
        #expect(links.count == 1)
        #expect(text(body, links[0].range) == link)
        #expect((body as NSString).substring(to: links[0].range.location).hasSuffix("ok "))
    }

    @Test func displayTextRoundTripsThroughEscaping() {
        let display = #"A|B [x] \ y"#
        let markup = NoteLinks.format(type: .recording, ref: uuid, display: display)
        #expect(markup == #"[[recording:\#(uuid)|A\|B \[x\] \\ y]]"#)
        let parsed = NoteLinks.parse("see \(markup)!")
        #expect(parsed.first?.display == display)
        #expect(NoteLinks.format(type: .artist, ref: uuid, display: "two\nlines") == "[[artist:\(uuid)|two lines]]")
    }
}

@Suite(.timeLimit(.minutes(1)))
struct NoteStylingTests {
    private func kinds(_ styling: NoteStyling, in body: String) -> [String: NoteStyling.Kind] {
        Dictionary(styling.spans.map { (text(body, $0.range), $0.kind) }, uniquingKeysWith: { _, last in last })
    }

    @Test func blocksAndInlineStylesWithSyntaxHiddenAwayFromTheCaret() {
        let body = "# Björk\n\n- **bold** and *it* and `code`\n> quote"
        let styling = NoteStyling(body as NSString, selection: NSRange(location: (body as NSString).length, length: 0))
        let spans = styling.spans
        #expect(spans.contains(.init(kind: .heading(level: 1), range: NSRange(location: 2, length: 5))))
        #expect(spans.contains(.init(kind: .hidden, range: NSRange(location: 0, length: 2))))  // "# "
        let found = kinds(styling, in: body)
        #expect(found["bold"] == .bold)
        #expect(found["it"] == .italic)
        #expect(found["code"] == .inlineCode)
        #expect(found["-"] == .bullet)
        #expect(found["**"] == .hidden)
        #expect(found["> "] == .marker)  // the caret is on the quote line
        #expect(spans.contains { $0.kind == .quote })
    }

    @Test func aLinkShowsOnlyItsDisplayTextAndBracketsWhenTheCaretIsInside() throws {
        let body = "Pairs with [[recording:\(uuid)|All Is Full of Love]] here"
        let ns = body as NSString
        let outside = NoteStyling(ns, selection: NSRange(location: 0, length: 0))
        let link = try #require(outside.links.first)
        #expect(outside.spans.contains(.init(kind: .link(index: 0), range: link.displayRange)))
        #expect(outside.spans.contains(.init(kind: .hidden, range: link.hiddenRange)))
        #expect(outside.spans.contains(.init(kind: .hidden, range: link.openRange)))

        let inside = NoteStyling(ns, selection: NSRange(location: link.displayRange.location + 3, length: 0))
        #expect(inside.spans.contains(.init(kind: .marker, range: link.openRange)))
        #expect(inside.spans.contains(.init(kind: .marker, range: link.closeRange)))
        #expect(inside.spans.contains(.init(kind: .hidden, range: link.hiddenRange)))  // the UUID never shows
        #expect(inside.linkIndex(at: link.displayRange.location) == 0)
    }

    @Test func theCaretNeverStopsInAHiddenLinkTarget() throws {
        let body = "x [[artist:\(uuid)|Björk]]"
        let styling = NoteStyling(body as NSString, selection: NSRange())
        let link = try #require(styling.links.first)
        let hidden = link.hiddenRange
        // Moving right into it: to the display text. Moving left out of the display text:
        // between the brackets.
        #expect(styling.adjustedCaret(hidden.location, from: hidden.location - 1) == NSMaxRange(hidden))
        #expect(styling.adjustedCaret(NSMaxRange(hidden) - 1, from: NSMaxRange(hidden)) == hidden.location - 1)
        #expect(styling.adjustedCaret(NSMaxRange(hidden) + 2, from: 0) == NSMaxRange(hidden) + 2)
    }
}

@Suite(.timeLimit(.minutes(1)))
struct LinkQueryTests {
    private func query(_ body: String, caret: Int? = nil) -> LinkQuery? {
        let ns = body as NSString
        let caret = caret ?? ns.length
        return LinkQuery.find(in: ns, caret: caret, styling: NoteStyling(ns, selection: NSRange(location: caret, length: 0)))
    }

    @Test func findsTheTextAfterAnOpenDoubleBracket() {
        let found = query("Co-producer on [[homgen")
        #expect(found?.text == "homgen")
        #expect(found?.range == NSRange(location: 15, length: 8))
        #expect(query("🎻 [[")?.text == "")
        #expect(query("🎻 [[")?.range == NSRange(location: 3, length: 2))
        // A "]]" right after the caret is replaced too.
        #expect(query("a [[jo]] b", caret: 6)?.range == NSRange(location: 2, length: 6))
    }

    @Test func noQueryAfterAClosedLinkALineBreakOrInCode() {
        #expect(query("[[artist:\(uuid)|A]] x") == nil)
        #expect(query("[[ab\ncd") == nil)
        #expect(query("`[[ab`", caret: 5) == nil)
        #expect(query("\\[[ab") == nil)
        #expect(query("[[a]] b") == nil)
    }
}

/// Answers `/server` and records the rest through `respond`.
private func connected(_ respond: @escaping @Sendable (HTTPRequest, Data?) -> (HTTPResponse.Status, String)) async
    -> (ServerConnection, StubTransport)
{
    let transport = StubTransport { request in
        if request.path == "/server" {
            return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(Fixtures.server.utf8))
        }
        let (status, json) = respond(request, nil)
        var fields: HTTPFields = [.contentType: "application/json"]
        // Lists carry their length, as the server's paged lists and `/search` do.
        if let list = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [Any] {
            fields[HTTPField.Name("X-Total-Count")!] = String(list.count)
        }
        return (HTTPResponse(status: status, headerFields: fields), Data(json.utf8))
    }
    let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: transport)
    await connection.connect()
    return (connection, transport)
}

nonisolated private func hit(_ type: String, _ id: String, _ title: String, extra: String = "") -> String {
    let entity = DetailFixtures.entity(type, id, title, subtitle: "Björk").dropLast()
    return
        #"\#(entity), "score": 1, "track_count": null, "release_group_count": null, "loose_recording_count": null, "radio_match": null\#(extra)}"#
}

@Suite(.timeLimit(.minutes(1)))
struct LinkAutocompleteTests {
    @Test func searchesTheLibraryAndChoosesWithReturnOrTab() async throws {
        let (connection, transport) = await connected { _, _ in
            (.ok, "[\(hit("release_group", "g1", "Homogenic")), \(hit("recording", "r1", "Hunter"))]")
        }
        let model = LinkAutocomplete(connection: connection, debounce: .zero)
        model.update(LinkQuery(range: NSRange(location: 0, length: 8), text: "homgen"))
        await model.settle()

        #expect(model.rows.count == 2)
        #expect(transport.requests.last?.request.path?.contains("q=homgen") == true)
        #expect(transport.requests.last?.request.path?.contains("include_radio_only=true") == true)
        model.move(1)
        guard case .insert(let summary, let keepTyping) = model.choice(for: .acceptAndContinue) else {
            Issue.record("expected an insert")
            return
        }
        #expect(summary.title == "Hunter" && summary.pill == "Track" && keepTyping)
        #expect(summary.link == "[[recording:r1|Hunter]]")
        #expect(model.choice(for: .alternate) == nil)  // ⌥↩ means nothing on a hit
    }

    @Test func withNoMatchTheActionsOfferANewArtistFirst() async throws {
        let (connection, _) = await connected { _, _ in (.ok, "[]") }
        let model = LinkAutocomplete(connection: connection, debounce: .zero)
        model.update(LinkQuery(range: NSRange(location: 0, length: 13), text: "Laurel Halo"))
        await model.settle()
        #expect(model.rows == [.newArtist("Laurel Halo"), .newOther("Laurel Halo"), .searchMusicBrainz("Laurel Halo")])
        #expect(model.choice(for: .accept) == .newArtist("Laurel Halo"))
        #expect(model.choice(for: .alternate) == .newOther("Laurel Halo"))
        #expect(model.choice(for: .shifted) == .searchMusicBrainz("Laurel Halo"))

        // Escape closes it until the caret leaves this "[[".
        model.dismiss()
        model.update(LinkQuery(range: NSRange(location: 0, length: 14), text: "Laurel Halo2"))
        #expect(!model.isOpen)
        model.update(nil)
        model.update(LinkQuery(range: NSRange(location: 0, length: 14), text: "Laurel Halo2"))
        #expect(model.isOpen)
    }
}

/// A `PUT /notes` stub: answers each save from `answers` in turn and records the bodies.
nonisolated private final class NoteServer: @unchecked Sendable {
    private let lock = NSLock()
    private var answers: [(HTTPResponse.Status, String)]
    private(set) var saved: [[String: Any]] = []

    init(_ answers: [(HTTPResponse.Status, String)]) { self.answers = answers }

    var savedBodies: [String] { lock.withLock { saved.compactMap { $0["body"] as? String } } }
    var savedBases: [String?] { lock.withLock { saved.map { $0["base_updated_at"] as? String } } }

    func transport() -> StubTransport {
        StubTransport { [self] request in
            if request.path == "/server" {
                return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(Fixtures.server.utf8))
            }
            return lock.withLock {
                let (status, json) = answers.isEmpty ? (.internalServerError, "{}") : answers.removeFirst()
                return (HTTPResponse(status: status, headerFields: [.contentType: "application/json"]), Data(json.utf8))
            }
        }
    }

    func record(_ transport: StubTransport) {
        lock.withLock {
            saved = transport.requests.filter { $0.request.method == .put }.compactMap {
                try? JSONSerialization.jsonObject(with: $0.body ?? Data()) as? [String: Any]
            }
        }
    }
}

@Suite(.timeLimit(.minutes(1)))
struct NoteDocumentTests {
    private static let t1 = "2026-11-01T12:00:00.000Z"
    private static let t2 = "2026-11-02T12:00:00.000Z"
    private static let ref = EntityRef(type: .artist, id: "a1")

    private func document(_ body: String, at stamp: String?, server: NoteServer) async throws -> (NoteDocument, StubTransport) {
        let transport = server.transport()
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: transport)
        await connection.connect()
        let note = try JSONDecoder.ep.decode(
            Components.Schemas.NoteRead.self, from: Data(DetailFixtures.note("artist", "a1", body: body, updatedAt: stamp).utf8))
        return (NoteDocument(note, ref: Self.ref, connection: connection, debounce: .milliseconds(20)), transport)
    }

    @Test func editsAreSavedOnceAfterTheDebounceFromTheVersionTheyStartedFrom() async throws {
        let link = #"{"range": {"start": 4, "end": 10}, "raw": "x", "target_type": "artist", "ref": "\#(uuid)", "display_text": null, "target": null}"#
        let server = NoteServer([(.ok, DetailFixtures.note("artist", "a1", body: "Hi there", updatedAt: Self.t2, links: link))])
        let (document, transport) = try await document("Hi", at: Self.t1, server: server)

        document.edit("Hi t")
        document.edit("Hi the")
        document.edit("Hi there")
        #expect(document.status == .editing)
        await document.settle()

        server.record(transport)
        #expect(server.savedBodies == ["Hi there"])
        #expect(server.savedBases == [Self.t1])
        #expect(document.status == .saved && !document.hasUnsavedChanges)
        // The server's answer says the link is broken.
        #expect(document.target(of: LinkKey(type: .artist, ref: uuid)) == .some(nil))
        #expect(document.baseUpdatedAt != nil)
    }

    @Test func aConflictKeepsTheEditUntilTheUserChooses() async throws {
        let theirs = DetailFixtures.note("artist", "a1", body: "Theirs", updatedAt: Self.t2)
        let server = NoteServer([
            (.conflict, #"{"detail": "changed", "current": \#(theirs)}"#),
            (.ok, DetailFixtures.note("artist", "a1", body: "Mine", updatedAt: "2026-11-03T12:00:00.000Z")),
        ])
        let (document, transport) = try await document("Old", at: Self.t1, server: server)

        document.edit("Mine")
        await document.settle()
        guard case .conflict(let current) = document.status else {
            Issue.record("expected a conflict, got \(document.status)")
            return
        }
        #expect(current.body == "Theirs")
        #expect(document.text == "Mine")
        document.edit("Mine!")  // no autosave while in conflict
        document.edit("Mine")
        await document.settle()

        document.keepMine()
        await document.settle()
        server.record(transport)
        #expect(server.savedBodies == ["Mine", "Mine"])
        #expect(server.savedBases == [Self.t1, Self.t2])  // saved over their version
        #expect(document.status == .saved)
    }

    @Test func takingTheirsReplacesTheTextAndAReloadNeverOverwritesAnEdit() async throws {
        let theirs = DetailFixtures.note("artist", "a1", body: "Theirs", updatedAt: Self.t2)
        let server = NoteServer([(.conflict, #"{"detail": "changed", "current": \#(theirs)}"#)])
        let (document, _) = try await document("Old", at: Self.t1, server: server)
        document.edit("Mine")
        await document.settle()
        let revision = document.revision
        document.takeTheirs()
        #expect(document.text == "Theirs" && document.revision == revision + 1 && document.status == .idle)

        // A reload while editing keeps the edit.
        document.edit("Theirs, edited")
        let reloaded = try JSONDecoder.ep.decode(
            Components.Schemas.NoteRead.self,
            from: Data(DetailFixtures.note("artist", "a1", body: "Remote", updatedAt: "2026-11-04T12:00:00.000Z").utf8))
        document.apply(reloaded)
        #expect(document.text == "Theirs, edited")
    }

    @Test func aSaveThatCannotReachTheServerKeepsTheEdit() async throws {
        let transport = StubTransport { request in
            if request.path == "/server" {
                return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(Fixtures.server.utf8))
            }
            throw URLError(.cannotConnectToHost)
        }
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: transport)
        await connection.connect()
        let note = try JSONDecoder.ep.decode(
            Components.Schemas.NoteRead.self, from: Data(DetailFixtures.note("artist", "a1", body: "Old").utf8))
        let document = NoteDocument(note, ref: Self.ref, connection: connection, debounce: .zero)
        document.edit("New")
        await document.settle()
        if case .failed = document.status {} else { Issue.record("expected failed, got \(document.status)") }
        #expect(document.hasUnsavedChanges && document.text == "New")
        #expect(!connection.isConnected)  // the banner shows; notes are read-only
        // Left behind, the edit waits (it is not dropped) and comes back with its entity.
        #expect(NoteDocument.orphan(for: Self.ref, connection: connection) === document)
        #expect(NoteDocument.unsaved.values.contains { $0 === document })
    }

    @Test func aReloadReadBeforeOurOwnSaveNeverBringsBackTheOldText() async throws {
        let server = NoteServer([(.ok, DetailFixtures.note("artist", "a1", body: "New", updatedAt: Self.t2))])
        let (document, _) = try await document("Old", at: Self.t1, server: server)
        document.edit("New")
        await document.settle()
        #expect(document.status == .saved)
        let stale = try JSONDecoder.ep.decode(
            Components.Schemas.NoteRead.self, from: Data(DetailFixtures.note("artist", "a1", body: "Old", updatedAt: Self.t1).utf8))
        document.apply(stale)
        #expect(document.text == "New" && document.baseUpdatedAt != nil)
    }
}

extension JSONDecoder {
    /// Decodes the API's JSON as the generated client does (fractional-second dates).
    static var ep: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let string = try decoder.singleValueContainer().decode(String.self)
            return try Date.ISO8601FormatStyle(includingFractionalSeconds: true).parse(string)
        }
        return decoder
    }
}
