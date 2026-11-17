import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

nonisolated enum EditFixtures {
    typealias F = LibraryFixtures

    static let credit = #"{"id": "c1", "name": "Björk feat. Robert Wyatt", "names": [{"artist_id": "a1", "credited_name": "Björk", "join_phrase": " feat. "}, {"artist_id": "a2", "credited_name": "Robert Wyatt", "join_phrase": ""}]}"#

    static let group =
        #"{"id": "g1", \#(F.stamp), "mbid": "3c1c0000-0000-0000-0000-00000000a92e", "release_mbid": null, "title": "Medúlla", "artist_credit": \#(F.credit("c0", "Björk", artists: ["a1"])), "type": "album", "date": "2004-08-30", "barcode": null, "track_count": 2, "disc_count": 1, "length_ms": 8000, "play_states": {"file": 2, "slice": 0, "radio_excerpt": 0, "file_missing": 0, "link_only": 0, "no_source": 0}, "link_kinds": [], "cover_url": null, "link": "l"}"#

    static func track(_ id: String, _ title: String, position: Int, credit: String = F.credit("c0", "Björk", artists: ["a1"])) -> String {
        #"{"id": "\#(id)", \#(F.stamp), "release_group_id": "g1", "disc": 1, "position": \#(position), "title": null, "recording": \#(F.recording("r-" + id, title, credit: credit)), "playability": \#(F.playability("file"))}"#
    }

    static let artist =
        #"{"id": "a1", \#(F.stamp), "mbid": "11111111-0000-0000-0000-000000000001", "name": "Björk", "sort_name": "Björk", "disambiguation": "", "type": "person", "counts": {"release_groups": 1, "loose_recordings": 0, "radio_episodes": 0}, "radio_only": false, "link": "l"}"#

    static func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try JSONDecoder.iso8601Fractional.decode(type, from: Data(json.utf8))
    }
}

private typealias E = EditFixtures

extension JSONDecoder {
    /// Decodes the server's timestamps (fractional seconds), for fixtures decoded directly.
    nonisolated static var iso8601Fractional: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let text = try decoder.singleValueContainer().decode(String.self)
            guard let date = try? Date(text, strategy: .iso8601.year().month().day().time(includingFractionalSeconds: true))
            else { throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: text)) }
            return date
        }
        return decoder
    }
}

/// Encodes a PATCH as it goes out: the body with the explicit nulls added.
private func sent<Body: Encodable, Key: CodingKey>(_ patch: Patch<Body, Key>?) throws -> NSDictionary? {
    guard let patch else { return nil }
    var object = try JSONSerialization.jsonObject(with: JSONEncoder().encode(patch.body)) as! [String: Any]
    for key in patch.nulls { object[key.stringValue] = NSNull() }
    return object as NSDictionary
}

@Suite(.timeLimit(.minutes(1)))
struct EditFormTests {
    @Test func anUnchangedAlbumSendsNothingAndChangesSendOnlyThemselves() throws {
        let group = try E.decode(Components.Schemas.ReleaseGroupDetail.self, E.group)
        let original = AlbumForm(group)
        var form = original
        #expect(form.patch(from: original) == nil)

        form.title = "Medúlla (2004)"
        form.date = ""  // cleared: an explicit null
        form.mbid = nil  // Detach
        #expect(
            try sent(form.patch(from: original))
                == ["title": "Medúlla (2004)", "date": NSNull(), "mbid": NSNull()])

        form.date = "2004-13"
        #expect(!form.isValid)
    }

    @Test func aCreditIsSentWholeWithItsJoiningTextAndNewArtists() throws {
        let group = try E.decode(Components.Schemas.ReleaseGroupDetail.self, E.group)
        let original = AlbumForm(group)
        var form = original
        form.credit[0].joinPhrase = " feat. "
        form.credit.append(CreditPart(newArtistName: "Robert Wyatt", name: "Robert Wyatt", joinPhrase: " & "))
        #expect(Credit.rendered(form.credit) == "Björk feat. Robert Wyatt")
        #expect(
            try sent(form.patch(from: original)) == [
                "artist_credit": [
                    ["artist_id": "a1", "credited_name": "Björk", "join_phrase": " feat. "],
                    ["join_phrase": "", "new_artist": ["name": "Robert Wyatt"]],
                ]
            ])
    }

    @Test func anArtistsTypeAndIDClearWithNulls() throws {
        let artist = try E.decode(Components.Schemas.ArtistDetail.self, E.artist)
        let original = ArtistForm(artist)
        var form = original
        form.type = nil
        form.mbid = nil
        form.disambiguation = "Icelandic singer"
        #expect(
            try sent(form.patch(from: original))
                == ["type": NSNull(), "mbid": NSNull(), "disambiguation": "Icelandic singer"])
        form.sortName = " "
        #expect(!form.isValid)
    }

    @Test func aTracksLengthIsReadAsATime() throws {
        #expect(LengthText.parse("4:15") == .some(255_000))
        #expect(LengthText.parse("1:02:03") == .some(3_723_000))
        #expect(LengthText.parse("") == .some(nil))
        #expect(LengthText.parse("4:x") == nil)
        let recording = try E.decode(
            Components.Schemas.RecordingDetail.self,
            String(LibraryFixtures.recording("r1", "Jóga", credit: E.credit).dropLast())
                + #", "playability": \#(LibraryFixtures.playability("file")), "tracks": []}"#)
        let original = RecordingForm(recording)
        var form = original
        form.length = "4:5"  // 245 s, as before
        #expect(form.patch(from: original) == nil)
        form.length = ""
        #expect(try sent(form.patch(from: original)) == ["length_ms": NSNull()])
    }

    @Test func theOrderIsNumberedPerDisc() {
        var rows = [
            TrackEditRow(newTitle: "A", disc: 1), TrackEditRow(newTitle: "B", disc: 1),
            TrackEditRow(newTitle: "C", disc: 2),
        ]
        rows[0].trackID = "t1"
        rows[1].trackID = "t2"
        rows[2].trackID = "t3"
        rows.swapAt(0, 1)
        let places = TrackOrder.places(rows)
        #expect(places.map(\.id) == ["t2", "t1", "t3"])
        #expect(places.map(\.position) == [1, 2, 1])
        #expect(TrackOrder.changed(rows, from: ["t1", "t2", "t3"]))
    }
}

@Suite(.timeLimit(.minutes(1)))
struct AlbumEditorTests {
    @Test func saveSendsThePatchTheChangedCreditAndTheNewOrder() async throws {
        let tracks = "[\(E.track("t1", "Pleasure Is All Mine", position: 1)), \(E.track("t2", "Where Is the Line", position: 2))]"
        let reordered = "[\(E.track("t2", "Where Is the Line", position: 1)), \(E.track("t1", "Pleasure Is All Mine", position: 2))]"
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server),
            "/release-groups/g1": (.ok, E.group),
            "/release-groups/g1/tracks": (.ok, tracks),
            "PATCH /release-groups/g1": (.ok, LibraryFixtures.releaseGroup("g1", "Medúlla!", artist: "a1")),
            "PATCH /recordings/r-t2": (.ok, LibraryFixtures.recording("r-t2", "Where Is the Line", credit: E.credit)),
            "PUT /release-groups/g1/track-order": (.ok, reordered),
        ])
        let editor = AlbumEditor(id: "g1", connection: await stubConnection(transport))
        await editor.load()
        #expect(editor.phase == .ready && !editor.hasChanges)

        editor.form?.title = "Medúlla!"
        editor.tracks[1].credit[0].joinPhrase = " feat. "
        editor.tracks[1].credit.append(CreditPart(artistID: "a2", name: "Robert Wyatt", joinPhrase: ""))
        editor.moveTracks(from: [1], to: 0)
        #expect(editor.hasChanges)
        #expect(await editor.save())

        let sent = transport.requests.filter { $0.request.method != .get }
        #expect(sent.map { "\($0.request.method.rawValue) \($0.request.path ?? "")" } == [
            "PATCH /release-groups/g1", "PATCH /recordings/r-t2", "PUT /release-groups/g1/track-order",
        ])
        let bodies = try sent.map { try JSONSerialization.jsonObject(with: #require($0.body)) as? NSDictionary }
        try #require(bodies.count == 3)
        #expect(bodies[0] == ["title": "Medúlla!"])
        #expect(
            bodies[1] == [
                "artist_credit": [
                    ["artist_id": "a1", "credited_name": "Björk", "join_phrase": " feat. "],
                    ["artist_id": "a2", "credited_name": "Robert Wyatt", "join_phrase": ""],
                ]
            ])
        #expect(
            bodies[2] == [
                "tracks": [["id": "t2", "disc": 1, "position": 1], ["id": "t1", "disc": 1, "position": 2]]
            ])
        #expect(editor.tracks.map(\.id) == ["t2", "t1"])
        #expect(!editor.hasChanges)
    }

    @Test func aRefusedSaveSaysSoAndKeepsTheEdit() async throws {
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server),
            "/release-groups/g1": (.ok, E.group),
            "/release-groups/g1/tracks": (.ok, "[]"),
            "PATCH /release-groups/g1": (.conflict, #"{"detail": "conflict"}"#),
        ])
        let editor = AlbumEditor(id: "g1", connection: await stubConnection(transport))
        await editor.load()
        editor.form?.title = "Other"
        #expect(await editor.save() == false)
        #expect(editor.error != nil)
        #expect(editor.form?.title == "Other" && editor.hasChanges)
    }
}

@Suite(.timeLimit(.minutes(1)))
struct CreatorTests {
    @Test func aRecordingByHandIsCreditedAndLinkable() async throws {
        let transport = methodRouted([
            "/server": (.ok, Fixtures.server),
            "POST /recordings": (
                .created, LibraryFixtures.recording("r9", "Hour Logic", credit: LibraryFixtures.credit("c9", "Laurel Halo", artists: ["a9"]))
            ),
        ])
        let creator = Creator(kind: .recording, name: " Hour Logic ", connection: await stubConnection(transport))
        #expect(!creator.isValid)  // no credit yet
        creator.credit = [CreditPart(newArtistName: "Laurel Halo", name: "Laurel Halo", joinPhrase: "")]
        creator.length = "7:02"
        let created = try #require(await creator.create())
        #expect(created.ref == EntityRef(type: .recording, id: "r9"))
        #expect(created.linkKey == LinkKey(type: .recording, ref: "r9"))
        let body = try JSONSerialization.jsonObject(with: #require(transport.requests.last?.body)) as? NSDictionary
        #expect(
            body == [
                "title": "Hour Logic", "length_ms": 422_000,
                "artist_credit": [["join_phrase": "", "new_artist": ["name": "Laurel Halo"]]],
            ])
    }
}
