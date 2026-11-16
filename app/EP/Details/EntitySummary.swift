import EPAPI
import Foundation

/// An entity as a mixed-type row shows it (link suggestions, backlinks; the search palette
/// later): name, second line, type pill, image. Mapped from `EntityRead` and
/// `SearchHitRead`, which carry the same facts.
struct EntitySummary: Identifiable, Hashable {
    var ref: EntityRef
    var title: String
    /// "Björk · 1997 · Album", "Björk · Homogenic · 4:15", "Late Night Tapes · 2 Feb 2025".
    var detail: String
    /// The type pill: Artist, Album, EP, Single, Track, Episode, Show.
    var pill: String
    var coverPath: String?
    /// The note link to it, as the server writes it.
    var link: String
    var linkKey: LinkKey

    var id: EntityRef { ref }
    /// Artist, show and episode images are circles; covers are rounded squares.
    var roundImage: Bool { [.artist, .radioShow, .radioEpisode].contains(ref.type) }

    init(_ entity: Components.Schemas.EntityRead) {
        self.init(
            type: entity._type, id: entity.id, title: entity.title, subtitle: entity.subtitle,
            releaseGroupType: entity.releaseGroupType, date: entity.date, lengthMS: entity.lengthMs,
            releaseGroupTitle: entity.releaseGroupTitle, coverPath: entity.coverUrl, link: entity.link,
            linkRef: entity.linkRef, counts: nil)
    }

    init(_ hit: Components.Schemas.SearchHitRead) {
        self.init(
            type: hit._type, id: hit.id, title: hit.title, subtitle: hit.subtitle,
            releaseGroupType: hit.releaseGroupType, date: hit.date, lengthMS: hit.lengthMs,
            releaseGroupTitle: hit.releaseGroupTitle, coverPath: hit.coverUrl, link: hit.link, linkRef: hit.linkRef,
            counts: (hit.releaseGroupCount, hit.looseRecordingCount))
    }

    private init(
        type: Components.Schemas.EntityType, id: String, title: String, subtitle: String?,
        releaseGroupType: Components.Schemas.ReleaseGroupType?, date: String?, lengthMS: Int?,
        releaseGroupTitle: String?, coverPath: String?, link: String, linkRef: String,
        counts: (releaseGroups: Int?, loose: Int?)?
    ) {
        ref = EntityRef(type: type, id: id)
        self.title = title
        self.coverPath = coverPath
        self.link = link
        linkKey = LinkKey(type: type, ref: linkRef)
        let parts: [String?]
        switch type {
        case .artist:
            var countParts: [String] = []
            if let n = counts?.releaseGroups, n > 0 {
                countParts.append(String(localized: "\(n) releases"))
            }
            if let n = counts?.loose, n > 0 {
                countParts.append(String(localized: "\(n) loose tracks"))
            }
            parts = [subtitle] + countParts
            pill = String(localized: "Artist")
        case .releaseGroup:
            parts = [subtitle, LibraryFormat.year(date), releaseGroupType?.displayName]
            pill = releaseGroupType.map { $0 == .ep || $0 == .single ? $0.displayName : String(localized: "Album") }
                ?? String(localized: "Album")
        case .recording:
            parts = [subtitle, releaseGroupTitle, LibraryFormat.duration(ms: lengthMS)]
            pill = String(localized: "Track")
        case .radioShow:
            parts = [subtitle]
            pill = String(localized: "Show")
        case .radioEpisode:
            parts = [subtitle, LibraryFormat.day(date)]
            pill = String(localized: "Episode")
        }
        detail = parts.compactMap { $0?.isEmpty == false ? $0 : nil }.joined(separator: " · ")
    }
}

/// An entity a note, tag or panel is about: its type and internal ID.
nonisolated struct EntityRef: Hashable, Codable, Sendable {
    var type: Components.Schemas.EntityType
    var id: String

    /// The Library entity to navigate to; nil for radio shows and episodes (Radio view).
    var libraryEntity: LibraryEntity? {
        switch type {
        case .artist: .artist(id)
        case .releaseGroup: .releaseGroup(id)
        case .recording: .recording(id)
        case .radioShow, .radioEpisode: nil
        }
    }
}

extension Components.Schemas.EntityType {
    /// The user-facing kind (design §13.21): ARTIST, ALBUM, TRACK, SHOW, EPISODE (shown uppercase).
    var kindLabel: String {
        switch self {
        case .artist: String(localized: "Artist")
        case .releaseGroup: String(localized: "Album")
        case .recording: String(localized: "Track")
        case .radioShow: String(localized: "Show")
        case .radioEpisode: String(localized: "Episode")
        }
    }
}

extension LibraryFormat {
    /// "2 Feb 2025" from "2025-02-02" (a whole date), in the user's locale.
    static func day(_ date: String?) -> String? {
        guard let date, let parsed = try? Date(date, strategy: .iso8601.year().month().day()) else { return date }
        return parsed.formatted(.dateTime.day().month(.abbreviated).year().locale(.current))
    }
}
