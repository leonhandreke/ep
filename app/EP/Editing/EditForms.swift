import EPAPI
import Foundation

// The edit sheets' forms: what the fields hold, and the PATCH body for what changed
// (server convention: an omitted field stays as it is, an explicit null clears a
// nullable one). Pure values, so the bodies can be tested without views.

/// A PATCH body with the keys to send as JSON `null` (`withExplicitNulls`); nil when
/// nothing changed.
struct Patch<Body, Key: CodingKey> {
    var body: Body
    var nulls: [Key]
}

/// One credited artist with the text that joins it to the next ("Björk", " feat. ",
/// "Robert Wyatt"): an artist in the library, or a new one created with the credit.
struct CreditPart: Identifiable, Equatable {
    let id = UUID()
    var artistID: String?
    /// A new artist (no `artistID`) is created with this name.
    var newArtistName: String?
    /// The name as credited (shown on the chip).
    var name: String
    var joinPhrase: String

    /// What is sent and compared; the chip's identity is not part of it.
    var value: [String?] { [artistID, newArtistName, name, joinPhrase] }

    static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id && lhs.value == rhs.value }
}

enum Credit {
    static func parts(_ credit: Components.Schemas.ArtistCreditRead) -> [CreditPart] {
        credit.names.map { CreditPart(artistID: $0.artistId, name: $0.creditedName, joinPhrase: $0.joinPhrase) }
    }

    /// "Björk feat. Robert Wyatt".
    static func rendered(_ parts: [CreditPart]) -> String {
        parts.enumerated().map { index, part in
            part.name + (index < parts.count - 1 ? part.joinPhrase : "")
        }.joined()
    }

    static func same(_ a: [CreditPart], _ b: [CreditPart]) -> Bool {
        a.map(\.value) == b.map(\.value)
    }

    /// The credit as the API takes it; the last part's joining text is dropped.
    static func write(_ parts: [CreditPart]) -> [Components.Schemas.ArtistCreditNameIn] {
        parts.enumerated().map { index, part in
            let join = index < parts.count - 1 ? part.joinPhrase : ""
            if let artistID = part.artistID {
                return .init(artistId: artistID, creditedName: part.name, joinPhrase: join)
            }
            return .init(joinPhrase: join, newArtist: .init(name: part.newArtistName ?? part.name))
        }
    }
}

/// Partial dates as the API stores them: `YYYY`, `YYYY-MM` or `YYYY-MM-DD`.
enum PartialDate {
    static func isValid(_ text: String) -> Bool {
        text.isEmpty || text.wholeMatch(of: /\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?/) != nil
    }
}

/// A length typed as "4:15" or "1:02:03" (or plain seconds): milliseconds; nil when empty.
enum LengthText {
    static func format(_ ms: Int?) -> String { LibraryFormat.duration(ms: ms) ?? "" }

    /// `.some(nil)` for an empty field, nil when it cannot be read.
    static func parse(_ text: String) -> Int?? {
        let text = text.trimmingCharacters(in: .whitespaces)
        if text.isEmpty { return .some(nil) }
        let parts = text.split(separator: ":", omittingEmptySubsequences: false).map { Int($0) }
        guard (1...3).contains(parts.count), parts.allSatisfy({ $0 != nil && $0! >= 0 }) else { return nil }
        let seconds = parts.reduce(0) { $0 * 60 + $1! }
        return .some(seconds * 1000)
    }
}

// MARK: - Album (release group)

struct AlbumForm: Equatable {
    var title: String
    var credit: [CreditPart]
    var type: Components.Schemas.ReleaseGroupType
    var date: String
    var mbid: String?

    init(_ group: Components.Schemas.ReleaseGroupDetail) {
        title = group.title
        credit = Credit.parts(group.artistCredit)
        type = group._type
        date = group.date ?? ""
        mbid = group.mbid
    }

    var isValid: Bool {
        !title.trimmingCharacters(in: .whitespaces).isEmpty && !credit.isEmpty && PartialDate.isValid(date)
    }

    typealias Keys = Components.Schemas.ReleaseGroupUpdate.CodingKeys

    /// The PATCH for what differs from `original`; nil when nothing does.
    func patch(from original: AlbumForm) -> Patch<Components.Schemas.ReleaseGroupUpdate, Keys>? {
        var body = Components.Schemas.ReleaseGroupUpdate()
        var nulls: [Keys] = []
        var changed = false
        let title = title.trimmingCharacters(in: .whitespaces)
        if title != original.title {
            body.title = title
            changed = true
        }
        if !Credit.same(credit, original.credit) {
            body.artistCredit = Credit.write(credit)
            changed = true
        }
        if type != original.type {
            body._type = type
            changed = true
        }
        if date != original.date {
            if date.isEmpty { nulls.append(.date) } else { body.date = date }
            changed = true
        }
        if mbid != original.mbid {
            if let mbid { body.mbid = mbid } else { nulls.append(.mbid) }
            changed = true
        }
        return changed ? Patch(body: body, nulls: nulls) : nil
    }
}

// MARK: - Artist

struct ArtistForm: Equatable {
    var name: String
    var sortName: String
    var disambiguation: String
    var type: Components.Schemas.ArtistType?
    var mbid: String?

    init(_ artist: Components.Schemas.ArtistDetail) {
        name = artist.name
        sortName = artist.sortName
        disambiguation = artist.disambiguation
        type = artist._type
        mbid = artist.mbid
    }

    var isValid: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && !sortName.trimmingCharacters(in: .whitespaces).isEmpty
    }

    typealias Keys = Components.Schemas.ArtistUpdate.CodingKeys

    func patch(from original: ArtistForm) -> Patch<Components.Schemas.ArtistUpdate, Keys>? {
        var body = Components.Schemas.ArtistUpdate()
        var nulls: [Keys] = []
        var changed = false
        let name = name.trimmingCharacters(in: .whitespaces)
        let sortName = sortName.trimmingCharacters(in: .whitespaces)
        if name != original.name {
            body.name = name
            changed = true
        }
        if sortName != original.sortName {
            body.sortName = sortName
            changed = true
        }
        if disambiguation != original.disambiguation {
            body.disambiguation = disambiguation
            changed = true
        }
        if type != original.type {
            if let type { body._type = type } else { nulls.append(._type) }
            changed = true
        }
        if mbid != original.mbid {
            if let mbid { body.mbid = mbid } else { nulls.append(.mbid) }
            changed = true
        }
        return changed ? Patch(body: body, nulls: nulls) : nil
    }
}

// MARK: - Track (recording)

struct RecordingForm: Equatable {
    var title: String
    var credit: [CreditPart]
    var length: String
    var mbid: String?

    init(_ recording: Components.Schemas.RecordingDetail) {
        title = recording.title
        credit = Credit.parts(recording.artistCredit)
        length = LengthText.format(recording.lengthMs)
        mbid = recording.mbid
    }

    var isValid: Bool {
        !title.trimmingCharacters(in: .whitespaces).isEmpty && !credit.isEmpty && LengthText.parse(length) != nil
    }

    typealias Keys = Components.Schemas.RecordingUpdate.CodingKeys

    func patch(from original: RecordingForm) -> Patch<Components.Schemas.RecordingUpdate, Keys>? {
        var body = Components.Schemas.RecordingUpdate()
        var nulls: [Keys] = []
        var changed = false
        let title = title.trimmingCharacters(in: .whitespaces)
        if title != original.title {
            body.title = title
            changed = true
        }
        if !Credit.same(credit, original.credit) {
            body.artistCredit = Credit.write(credit)
            changed = true
        }
        // Compared as times, so "4:05" and "4:5" are no change.
        if let new = LengthText.parse(length), new != LengthText.parse(original.length) {
            if let ms = new { body.lengthMs = ms } else { nulls.append(.lengthMs) }
            changed = true
        }
        if mbid != original.mbid {
            if let mbid { body.mbid = mbid } else { nulls.append(.mbid) }
            changed = true
        }
        return changed ? Patch(body: body, nulls: nulls) : nil
    }
}

// MARK: - Track order

/// A row of the album sheet's track table: an existing track, or one added with
/// "+ Track" (created on Save).
struct TrackEditRow: Identifiable, Equatable {
    let id: String
    /// Nil for a track added in the sheet.
    var trackID: String?
    var recordingID: String?
    var disc: Int
    var title: String
    var credit: [CreditPart]
    var originalCredit: [CreditPart]
    var lengthMS: Int?

    init(_ track: Components.Schemas.TrackRead) {
        id = track.id
        trackID = track.id
        recordingID = track.recording.id
        disc = track.disc
        title = track.title ?? track.recording.title
        credit = Credit.parts(track.recording.artistCredit)
        originalCredit = credit
        lengthMS = track.recording.lengthMs
    }

    init(newTitle: String, disc: Int) {
        id = UUID().uuidString
        self.disc = disc
        title = newTitle
        credit = []
        originalCredit = []
    }

    var creditChanged: Bool { trackID != nil && !Credit.same(credit, originalCredit) }
}

enum TrackOrder {
    /// Every track's place for `PUT /release-groups/{id}/track-order`: numbered 1… per
    /// disc in the order shown. `ids` maps new rows to the tracks created for them.
    static func places(_ rows: [TrackEditRow], ids: [String: String] = [:]) -> [Components.Schemas.TrackPlace] {
        var next: [Int: Int] = [:]
        return rows.compactMap { row in
            guard let id = row.trackID ?? ids[row.id] else { return nil }
            let position = (next[row.disc] ?? 0) + 1
            next[row.disc] = position
            return .init(disc: row.disc, id: id, position: position)
        }
    }

    /// Whether the order differs from how the tracks came (`original`: track IDs in order).
    static func changed(_ rows: [TrackEditRow], from original: [String]) -> Bool {
        rows.compactMap(\.trackID) != original || rows.contains { $0.trackID == nil }
    }
}
