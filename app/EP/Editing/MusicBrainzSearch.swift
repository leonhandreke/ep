import EPAPI
import Foundation
import Observation

/// "Search MusicBrainz" (the note autocomplete's ⇧↩): searches MusicBrainz through the
/// server (`GET /musicbrainz/search`) and adds the chosen artist, release or recording to
/// the library as metadata (`POST /musicbrainz/import`). Searches run on Return, not per
/// keystroke: MusicBrainz allows one request a second.
@Observable
final class MusicBrainzSearch {
    var type: Components.Schemas.MusicBrainzSearchType = .artist
    var query: String
    private(set) var hits: [Components.Schemas.MusicBrainzHitRead] = []
    private(set) var isSearching = false
    private(set) var isImporting = false
    /// What the last search or import said went wrong.
    private(set) var error: String?
    /// The query and type the hits are for.
    private(set) var searched: (String, Components.Schemas.MusicBrainzSearchType)?

    @ObservationIgnored private let connection: ServerConnection

    init(query: String, connection: ServerConnection) {
        self.query = query
        self.connection = connection
    }

    func search() async {
        let text = query.trimmingCharacters(in: .whitespaces)
        guard !text.isEmpty else { return }
        isSearching = true
        error = nil
        defer { isSearching = false }
        let type = type
        do {
            hits = try await connection.client.searchMusicbrainz(query: .init(_type: type, q: text, limit: 15)).ok.body
                .json
            searched = (text, type)
        } catch {
            hits = []
            self.error = String(localized: "MusicBrainz couldn’t be searched.")
            connection.reportFailure(error)
        }
    }

    /// Adds the hit to the library; nil when that failed (`error` says so).
    func add(_ hit: Components.Schemas.MusicBrainzHitRead) async -> CreatedEntity? {
        isImporting = true
        error = nil
        defer { isImporting = false }
        do {
            let entity = try await connection.client.importFromMusicbrainz(
                body: .json(.init(mbid: hit.mbid, _type: hit._type))
            ).ok.body.json
            return CreatedEntity(entity)
        } catch {
            self.error = String(localized: "Couldn’t add it from MusicBrainz.")
            connection.reportFailure(error)
            return nil
        }
    }
}
