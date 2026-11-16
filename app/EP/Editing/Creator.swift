import EPAPI
import Foundation
import Observation

/// Something just added to the library (by hand or from MusicBrainz): enough to show it
/// or to link to it from a note.
struct CreatedEntity: Equatable {
    var ref: EntityRef
    var title: String
    /// `[[type:uuid|Title]]`, as the server writes it.
    var link: String
    /// The UUID in the link (MBID if there is one, else the ID).
    var linkRef: String

    var linkKey: LinkKey { LinkKey(type: ref.type, ref: linkRef) }
    var linkTarget: LinkTarget { LinkTarget(ref: ref, title: title) }

    init(_ entity: Components.Schemas.EntityRead) {
        ref = EntityRef(type: entity._type, id: entity.id)
        title = entity.title
        link = entity.link
        linkRef = entity.linkRef
    }

    init(ref: EntityRef, title: String, link: String, mbid: String?) {
        self.ref = ref
        self.title = title
        self.link = link
        linkRef = mbid ?? ref.id
    }
}

/// Adding an artist, an album or a recording by hand (File ▸ New…, "Add album by hand…",
/// and the note autocomplete's "New album or recording…").
@Observable
final class Creator {
    enum Kind: String, CaseIterable, Identifiable {
        case artist, album, recording
        var id: Self { self }

        var title: String {
            switch self {
            case .artist: String(localized: "Artist")
            case .album: String(localized: "Album")
            case .recording: String(localized: "Recording")
            }
        }
    }

    var kind: Kind
    var name: String
    // Artist
    var sortName = ""
    var disambiguation = ""
    var artistType: Components.Schemas.ArtistType?
    // Album and recording
    var credit: [CreditPart] = []
    var albumType = Components.Schemas.ReleaseGroupType.album
    var date = ""
    var length = ""

    private(set) var isSaving = false
    private(set) var error: String?

    @ObservationIgnored private let connection: ServerConnection

    init(kind: Kind, name: String = "", credit: [CreditPart] = [], connection: ServerConnection) {
        self.kind = kind
        self.name = name
        self.credit = credit
        self.connection = connection
    }

    private var trimmedName: String { name.trimmingCharacters(in: .whitespaces) }

    var isValid: Bool {
        guard !trimmedName.isEmpty else { return false }
        switch kind {
        case .artist: return true
        case .album: return !credit.isEmpty && PartialDate.isValid(date)
        case .recording: return !credit.isEmpty && LengthText.parse(length) != nil
        }
    }

    /// Creates it; nil when the server refused (the reason is in `error`).
    func create() async -> CreatedEntity? {
        guard isValid else { return nil }
        isSaving = true
        error = nil
        defer { isSaving = false }
        let client = connection.client
        do {
            switch kind {
            case .artist:
                let sort = sortName.trimmingCharacters(in: .whitespaces)
                let artist = try await client.createArtist(
                    body: .json(
                        .init(
                            disambiguation: disambiguation.isEmpty ? nil : disambiguation, name: trimmedName,
                            sortName: sort.isEmpty ? nil : sort, _type: artistType))
                ).created.body.json
                return CreatedEntity(
                    ref: EntityRef(type: .artist, id: artist.id), title: artist.name, link: artist.link, mbid: artist.mbid)
            case .album:
                let group = try await client.createReleaseGroup(
                    body: .json(
                        .init(
                            artistCredit: Credit.write(credit), date: date.isEmpty ? nil : date, title: trimmedName,
                            _type: albumType))
                ).created.body.json
                return CreatedEntity(
                    ref: EntityRef(type: .releaseGroup, id: group.id), title: group.title, link: group.link,
                    mbid: group.mbid)
            case .recording:
                let lengthMS = LengthText.parse(length) ?? nil
                let recording = try await client.createRecording(
                    body: .json(.init(artistCredit: Credit.write(credit), lengthMs: lengthMS, title: trimmedName))
                ).created.body.json
                return CreatedEntity(
                    ref: EntityRef(type: .recording, id: recording.id), title: recording.title, link: recording.link,
                    mbid: recording.mbid)
            }
        } catch {
            self.error = EditErrors.describe(error)
            connection.reportFailure(error)
            return nil
        }
    }
}
