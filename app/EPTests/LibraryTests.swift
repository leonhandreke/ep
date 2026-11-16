import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

/// Answers by path (without the query) from a table of JSON bodies; 404 otherwise.
nonisolated func routed(_ routes: [String: String]) -> StubTransport {
    StubTransport { request in
        let path = request.path?.split(separator: "?").first.map(String.init) ?? ""
        guard let json = routes[path] else {
            return (HTTPResponse(status: .notFound, headerFields: [.contentType: "application/json"]), nil)
        }
        return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(json.utf8))
    }
}

/// JSON for the generated types, with only the fields a test cares about varying.
nonisolated enum LibraryFixtures {
    static let stamp = #""created_at": "2026-11-01T12:00:00.000Z", "updated_at": "2026-11-01T12:00:00.000Z""#

    static func credit(_ id: String, _ name: String, artists: [String]) -> String {
        let names = artists.map { #"{"artist_id": "\#($0)", "credited_name": "\#(name)", "join_phrase": ""}"# }
        return #"{"id": "\#(id)", "name": "\#(name)", "names": [\#(names.joined(separator: ","))]}"#
    }

    static func artist(_ id: String, _ name: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "mbid": null, "name": "\#(name)", "sort_name": "\#(name)", "disambiguation": "", "type": null, "link": "[[artist:\#(id)|\#(name)]]"}"#
    }

    static func artistDetail(_ id: String, _ name: String, releaseGroups: Int = 1, loose: Int = 0) -> String {
        #"{"id": "\#(id)", \#(stamp), "mbid": null, "name": "\#(name)", "sort_name": "\#(name)", "disambiguation": "", "type": null, "counts": {"release_groups": \#(releaseGroups), "loose_recordings": \#(loose), "radio_episodes": 0}, "radio_only": false, "link": "l"}"#
    }

    static func releaseGroup(_ id: String, _ title: String, artist: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "mbid": null, "release_mbid": null, "title": "\#(title)", "artist_credit": \#(credit("c-" + artist, "A", artists: [artist])), "type": "album", "date": "1997-09-22", "barcode": null, "cover_url": null, "link": "l"}"#
    }

    static func releaseGroupDetail(_ id: String, _ title: String, artist: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "mbid": null, "release_mbid": null, "title": "\#(title)", "artist_credit": \#(credit("c-" + artist, "A", artists: [artist])), "type": "album", "date": "1997-09-22", "barcode": null, "track_count": 1, "disc_count": 1, "length_ms": 4000, "play_states": {"file": 1, "slice": 0, "radio_excerpt": 0, "file_missing": 0, "link_only": 0, "no_source": 0}, "link_kinds": [], "cover_url": null, "link": "l"}"#
    }

    static func playability(_ state: String, link: String? = nil) -> String {
        let linkJSON = link.map { #"{"source_id": "s", "kind": "\#($0)", "url": "https://x"}"# } ?? "null"
        return
            #"{"state": "\#(state)", "playback": null, "radio_entry": null, "link": \#(linkJSON), "missing_file_source_id": null}"#
    }

    static func recording(_ id: String, _ title: String, credit: String) -> String {
        #"{"id": "\#(id)", \#(stamp), "mbid": null, "title": "\#(title)", "artist_credit": \#(credit), "length_ms": 245000, "preferred_source_id": null, "link": "[[recording:\#(id)|\#(title)]]"}"#
    }

    static func track(
        _ id: String, _ title: String, releaseGroup: String, artist: String, state: String = "file",
        link: String? = nil, credit: String? = nil
    ) -> String {
        let credit = credit ?? Self.credit("c-\(artist)", "A", artists: [artist])
        return
            #"{"id": "\#(id)", \#(stamp), "release_group_id": "\#(releaseGroup)", "disc": 1, "position": 1, "title": null, "recording": \#(recording("r-" + id, title, credit: credit)), "playability": \#(playability(state, link: link))}"#
    }
}

private typealias F = LibraryFixtures

private func connection(_ transport: StubTransport) -> ServerConnection {
    ServerConnection(serverURL: URL(string: "http://ep.test")!, transport: transport)
}

private func queries(_ transport: StubTransport, path: String) -> [String] {
    transport.requests.compactMap(\.request.path).filter { $0.hasPrefix(path + "?") || $0 == path }
}

struct ArtistListTests {
    @Test func filterIsDebouncedAndOnlyTheLastQueryRuns() async throws {
        let transport = routed(["/artists": "[\(F.artist("a1", "Björk"))]"])
        let list = ArtistList(connection: connection(transport), debounce: .milliseconds(50))

        list.setFilter("b")
        list.setFilter("bj")
        list.setFilter("bjo")
        await list.pendingLoad?.value

        let sent = queries(transport, path: "/artists")
        #expect(sent.count == 1)
        #expect(sent.first?.contains("q=bjo") == true)
        #expect(list.artists.map(\.name) == ["Björk"])
        #expect(list.phase == .loaded)
    }

    @Test func aFullPageMeansMoreAndTheNextPageAppends() async throws {
        let transport = StubTransport { request in
            let offset = request.path?.contains("offset=2") == true
            let body = offset ? "[\(F.artist("a3", "C"))]" : "[\(F.artist("a1", "A")), \(F.artist("a2", "B"))]"
            return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(body.utf8))
        }
        let list = ArtistList(connection: connection(transport), pageSize: 2)
        list.reload()
        await list.pendingLoad?.value
        #expect(list.canLoadMore)

        list.loadMoreIfNeeded(after: "a2")
        await list.pendingLoad?.value
        #expect(list.artists.map(\.id) == ["a1", "a2", "a3"])
        #expect(!list.canLoadMore)
    }

    @Test func aFailedReloadKeepsWhatIsShown() async throws {
        let transport = routed(["/artists": "[\(F.artist("a1", "Björk"))]"])
        let list = ArtistList(connection: connection(transport))
        list.reload()
        await list.pendingLoad?.value

        let failing = ArtistList(connection: connection(StubTransport { _ in throw URLError(.cannotConnectToHost) }))
        failing.reload()
        await failing.pendingLoad?.value
        #expect(failing.artists.isEmpty)
        if case .failed = failing.phase {} else { Issue.record("expected failed, got \(failing.phase)") }
        #expect(list.artists.count == 1)
    }

    @Test func typeToSelectIgnoresCaseAccentsAndPunctuation() async throws {
        let transport = routed([
            "/artists": "[\(F.artist("a1", "Arca")), \(F.artist("a2", "Björk")), \(F.artist("a3", "Glenn-Copeland"))]"
        ])
        let list = ArtistList(connection: connection(transport))
        list.reload()
        await list.pendingLoad?.value
        #expect(list.firstArtist(matching: "BJO")?.id == "a2")
        #expect(list.firstArtist(matching: "glennc")?.id == "a3")
        #expect(list.firstArtist(matching: "z") == nil)
    }
}

struct LibraryBrowserTests {
    static let routes: [String: String] = [
        "/artists/a1": F.artistDetail("a1", "Björk"),
        "/artists/a1/release-groups": "[\(F.releaseGroup("g1", "Homogenic", artist: "a1"))]",
        "/release-groups/g1": F.releaseGroupDetail("g1", "Homogenic", artist: "a1"),
        "/release-groups/g1/tracks": "[\(F.track("t1", "Jóga", releaseGroup: "g1", artist: "a1"))]",
        "/tracks/t1": F.track("t1", "Jóga", releaseGroup: "g1", artist: "a1"),
    ]

    @Test func selectionLoadsLeftToRight() async throws {
        let transport = routed(Self.routes)
        let browser = LibraryBrowser(connection: connection(transport))

        browser.select(artist: "a1")
        await browser.settle()
        #expect(browser.artist?.name == "Björk")
        #expect(browser.releaseGroups.map(\.title) == ["Homogenic"])
        #expect(browser.releaseGroupsPhase == .loaded)
        #expect(browser.tracksPhase == .idle)

        browser.select(album: .releaseGroup("g1"))
        await browser.settle()
        #expect(browser.releaseGroup?.title == "Homogenic")
        #expect(browser.tracks.map(\.title) == ["Jóga"])

        browser.select(track: "t1")
        #expect(browser.selectedTrack?.title == "Jóga")

        // A new artist clears the album and its tracks.
        browser.select(artist: "a2")
        #expect(browser.location == LibraryLocation(artistID: "a2"))
        #expect(browser.tracks.isEmpty)
    }

    @Test func historyGoesBackAndForwardAndForgetsForwardOnANewPath() async throws {
        let transport = routed(Self.routes)
        var now = ContinuousClock.now
        let browser = LibraryBrowser(connection: connection(transport), clock: { now })

        browser.select(artist: "a1")
        now += .seconds(2)
        browser.select(album: .releaseGroup("g1"))
        browser.select(track: "t1")  // not a history entry of its own
        await browser.settle()
        #expect(browser.canGoBack && !browser.canGoForward)

        browser.goBack()
        #expect(browser.location == LibraryLocation(artistID: "a1"))
        browser.goForward()
        #expect(browser.location == LibraryLocation(artistID: "a1", album: .releaseGroup("g1"), trackID: "t1"))
        await browser.settle()
        #expect(browser.tracks.count == 1)

        browser.goBack()
        browser.goBack()
        #expect(browser.location == LibraryLocation())
        #expect(!browser.canGoBack)
        browser.select(artist: "a2")
        #expect(!browser.canGoForward)
    }

    @Test func quickChangesInOneColumnCoalesce() {
        var history = NavigationHistory()
        let start = ContinuousClock.now
        history.push(LibraryLocation(artistID: "a1"), column: .artist, at: start)
        history.push(LibraryLocation(artistID: "a2"), column: .artist, at: start + .milliseconds(100))
        history.push(LibraryLocation(artistID: "a3"), column: .artist, at: start + .milliseconds(200))
        #expect(history.entries.count == 2)
        history.push(LibraryLocation(artistID: "a4"), column: .artist, at: start + .seconds(5))
        #expect(history.entries.map(\.artistID) == [nil, "a3", "a4"])
    }

    @Test func navigateResolvesTheArtistAlbumTrackPath() async throws {
        let recording = F.recording(
            "r1", "Jóga", credit: F.credit("c-a1", "Björk", artists: ["a1"])
        ).dropLast()
        let onAlbum = """
            \(recording), "playability": \(F.playability("file")), "tracks": [{"id": "t1", \(F.stamp), "disc": 1,
              "position": 2, "title": null, "release_group": \(F.releaseGroup("g1", "Homogenic", artist: "a1"))}]}
            """
        let loose = """
            \(F.recording("r2", "Demo", credit: F.credit("c-a1", "Björk", artists: ["a1"])).dropLast()),
              "playability": \(F.playability("link_only", link: "soundcloud")), "tracks": []}
            """
        var routes = Self.routes
        routes["/recordings/r1"] = onAlbum
        routes["/recordings/r2"] = loose
        let browser = LibraryBrowser(connection: connection(routed(routes)))

        #expect(
            try await browser.resolve(.track("t1")) == .init(artistID: "a1", album: .releaseGroup("g1"), trackID: "t1"))
        #expect(
            try await browser.resolve(.recording("r1"))
                == .init(artistID: "a1", album: .releaseGroup("g1"), trackID: "t1"))
        #expect(
            try await browser.resolve(.recording("r2")) == .init(artistID: "a1", album: .looseTracks, trackID: "r2"))

        browser.navigate(to: .track("t1"))
        await browser.settle()
        #expect(browser.location == LibraryLocation(artistID: "a1", album: .releaseGroup("g1"), trackID: "t1"))
        #expect(browser.tracks.first?.id == "t1")
        #expect(browser.canGoBack)
    }

    @Test func aFailedNavigationLeavesTheLocationAndReportsUnreachable() async throws {
        let transport = StubTransport { request in
            if request.path == "/server" {
                return (
                    HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]),
                    Data(Fixtures.server.utf8)
                )
            }
            throw URLError(.cannotConnectToHost)
        }
        let connection = connection(transport)
        await connection.connect()
        let browser = LibraryBrowser(connection: connection)

        browser.navigate(to: .track("t1"))
        await browser.settle()
        #expect(browser.location == LibraryLocation())
        #expect(!connection.isConnected)
    }
}

struct TrackRowTests {
    /// A row from a track decoded by the generated client (its date decoding).
    private func row(_ state: String, link: String? = nil, credit: String? = nil) async throws -> TrackRow {
        let json = F.track("t", "T", releaseGroup: "g", artist: "a", state: state, link: link, credit: credit)
        let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: StubTransport(json: json))
        let track = try await client.getTrack(path: .init(id: "t")).ok.body.json
        return TrackRow(track: track, albumCreditID: "c-a")
    }

    @Test func everyPlayStateMapsToItsNoteAndTreatment() async throws {
        #expect(try await row("file").note == nil)
        #expect(try await row("slice").note == nil)
        #expect(try await row("radio_excerpt").note?.text == "only in a radio episode")
        #expect(try await row("link_only", link: "bandcamp").note?.text == "Bandcamp link")
        #expect(try await row("no_source").note?.text == "no source")
        let missing = try await row("file_missing")
        #expect(missing.note == TrackRow.Note(text: "file missing", isDanger: true))
        #expect(missing.isDimmed && !missing.isItalic)
        #expect(try await row("no_source").isItalic)
        #expect(try await row("file").isDimmed == false)
        #expect(Components.Schemas.PlayState.allCases.filter(\.plays) == [.file, .slice, .radioExcerpt])
    }

    @Test func aDifferingCreditFillsTheNoteColumn() async throws {
        let featuring = F.credit("c-feat", "Björk feat. Mark Bell", artists: ["a", "b"])
        #expect(
            try await row("file", credit: featuring).note
                == TrackRow.Note(text: "Björk feat. Mark Bell", isCredit: true))
        // The state wins over the credit.
        #expect(try await row("no_source", credit: featuring).note?.text == "no source")
    }

    @Test func formatsYearsAndLengths() {
        #expect(LibraryFormat.year("1997-09-22") == "1997")
        #expect(LibraryFormat.year(nil) == nil)
        #expect(LibraryFormat.duration(ms: 245_000) == "4:05")
        #expect(LibraryFormat.duration(ms: 3_723_000) == "1:02:03")
    }
}
