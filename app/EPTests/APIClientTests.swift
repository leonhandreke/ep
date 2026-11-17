import CryptoKit
import EPAPI
import Foundation
import HTTPTypes
import Testing

@testable import EP

/// The generated client configured for the server's conventions (server/README.md).
@Suite(.timeLimit(.minutes(1)))
struct APIClientTests {
    @Test func decodesFractionalSecondTimestampsAndNulls() async throws {
        let transport = StubTransport(
            json: """
                {"entity_type": "artist", "entity_id": "5b11f4ce-a62d-471e-81fc-a69a8278c7da",
                 "body": "Hello", "updated_at": "2026-11-15T21:02:07.123Z", "links": []}
                """
        )
        let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: transport)

        let note = try await client.getNote(path: .init(_type: .artist, id: "5b11f4ce-a62d-471e-81fc-a69a8278c7da"))
            .ok.body.json

        let expected = Date(timeIntervalSince1970: 1_794_776_527.123)  // 2026-11-15T21:02:07.123Z
        #expect(try abs(#require(note.updatedAt).timeIntervalSince(expected)) < 0.0005)

        // A nullable field (anyOf with null in the document) decodes null as nil.
        let empty = StubTransport(
            json: """
                {"entity_type": "artist", "entity_id": "5b11f4ce-a62d-471e-81fc-a69a8278c7da",
                 "body": "", "updated_at": null, "links": []}
                """
        )
        let emptyNote = try await Client.ep(serverURL: URL(string: "http://ep.test")!, transport: empty)
            .getNote(path: .init(_type: .artist, id: "5b11f4ce-a62d-471e-81fc-a69a8278c7da"))
            .ok.body.json
        #expect(emptyNote.updatedAt == nil)
    }

    @Test func explicitNullsClearFieldsInPatchBodies() async throws {
        let transport = StubTransport { _ in throw CancellationError() }
        let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: transport)
        typealias Keys = Components.Schemas.RecordingUpdate.CodingKeys

        _ = try? await withExplicitNulls(Keys.lengthMs) {
            try await client.updateRecording(path: .init(id: "r1"), body: .json(.init(title: "Jóga")))
        }
        // Outside the closure, nil is left out as usual.
        _ = try? await client.updateRecording(path: .init(id: "r1"), body: .json(.init(title: "Jóga")))

        let bodies = try transport.requests.map {
            try JSONSerialization.jsonObject(with: #require($0.body)) as? NSDictionary
        }
        try #require(bodies.count == 2)
        #expect(bodies[0] == ["title": "Jóga", "length_ms": NSNull()])
        #expect(bodies[1] == ["title": "Jóga"])
        #expect(transport.requests[0].request.headerFields[.contentLength] == String(transport.requests[0].body!.count))
    }

    @Test func explicitNullsNestAndKeepNestedValues() async throws {
        let transport = StubTransport { _ in throw CancellationError() }
        let client = Client.ep(serverURL: URL(string: "http://ep.test")!, transport: transport)
        typealias Keys = Components.Schemas.RecordingUpdate.CodingKeys
        let credit = Components.Schemas.ArtistCreditNameIn(artistId: "a1", joinPhrase: " & ")

        _ = try? await withExplicitNulls(Keys.mbid) {
            try await withExplicitNulls(Keys.lengthMs) {
                try await client.updateRecording(path: .init(id: "r1"), body: .json(.init(artistCredit: [credit])))
            }
        }

        let body = try JSONSerialization.jsonObject(with: #require(transport.requests.first?.body)) as? NSDictionary
        #expect(
            body == [
                "mbid": NSNull(), "length_ms": NSNull(),
                "artist_credit": [["artist_id": "a1", "join_phrase": " & "]],
            ]
        )
    }
}

/// The checked-in client (Packages/EPAPI/Sources/EPAPI/Generated) matches the server's
/// document, like the server's own openapi.json staleness test.
@Suite(.timeLimit(.minutes(1)))
struct OpenAPIDocumentTests {
    @Test func clientIsGeneratedFromTheCurrentDocument() throws {
        let document = URL(filePath: #filePath)
            .deletingLastPathComponent()
            .appending(path: "../../server/openapi.json")
        let digest = SHA256.hash(data: try Data(contentsOf: document))
        let sha256 = digest.map { String(format: "%02x", $0) }.joined()
        #expect(sha256 == OpenAPIDocument.sha256, "server/openapi.json changed: run app/scripts/generate-api.sh")
    }
}
