import EPAPI
import Foundation

/// One row of the tracks column, from an album's track or a loose recording: what the
/// row shows, mapped from the generated types (no decoding of its own).
struct TrackRow: Identifiable, Hashable {
    /// The track's ID in an album; the recording's ID among loose tracks.
    let id: String
    let recordingID: String
    let disc: Int
    /// Position on its disc; nil for loose tracks.
    let number: Int?
    let title: String
    let lengthMS: Int?
    let playState: Components.Schemas.PlayState
    /// How the row plays (the player's input: what to stream, the radio entry, the link).
    let playability: Components.Schemas.PlayabilityRead
    /// The recording's artist credit, as shown ("Björk feat. Mark Bell").
    let artistName: String
    /// The credit's first artist (the player's artist link).
    let artistID: String?
    /// The 160 pt note column: the state, or a credit that differs from the album's.
    let note: Note?
    /// The note link to the recording ("Copy Link").
    let link: String
    /// The recording has a note (the note icon after the title).
    let hasNote: Bool

    struct Note: Hashable {
        var text: String
        var isDanger = false
        /// A differing artist credit (12 pt `--text2` in board 5) rather than a state.
        var isCredit = false
    }

    init(track: Components.Schemas.TrackRead, albumCreditID: String) {
        let recording = track.recording
        id = track.id
        recordingID = recording.id
        disc = track.disc
        number = track.position
        title = track.title ?? recording.title
        lengthMS = recording.lengthMs
        playState = track.playability.state
        playability = track.playability
        artistName = recording.artistCredit.name
        self.artistID = recording.artistCredit.names.first?.artistId
        link = recording.link
        hasNote = recording.hasNote
        let credit = recording.artistCredit
        note = Self.note(
            for: track.playability,
            credit: credit.id == albumCreditID ? nil : credit.name
        )
    }

    init(looseRecording recording: Components.Schemas.PlayableRecordingRead, artistID: String) {
        id = recording.id
        recordingID = recording.id
        disc = 1
        number = nil
        title = recording.title
        lengthMS = recording.lengthMs
        playState = recording.playability.state
        playability = recording.playability
        artistName = recording.artistCredit.name
        self.artistID = recording.artistCredit.names.first?.artistId
        link = recording.link
        hasNote = recording.hasNote
        let names = recording.artistCredit.names
        let differs = names.count != 1 || names.first?.artistId != artistID
        note = Self.note(for: recording.playability, credit: differs ? recording.artistCredit.name : nil)
    }

    /// A recording on its own (played from search): shown and played like a loose track.
    init(recording: Components.Schemas.RecordingDetail) {
        id = recording.id
        recordingID = recording.id
        disc = 1
        number = nil
        title = recording.title
        lengthMS = recording.lengthMs
        playState = recording.playability.state
        playability = recording.playability
        artistName = recording.artistCredit.name
        self.artistID = recording.artistCredit.names.first?.artistId
        link = recording.link
        hasNote = recording.hasNote
        note = Self.note(for: recording.playability, credit: nil)
    }

    /// State text first ("only in a radio episode", "Bandcamp link", "no source", "file
    /// missing"); for rows that play from a file, the differing credit.
    private static func note(for playability: Components.Schemas.PlayabilityRead, credit: String?) -> Note? {
        switch playability.state {
        case .radioExcerpt: Note(text: String(localized: "only in a radio episode"))
        case .linkOnly:
            Note(
                text: playability.link.map { String(localized: "\($0.kind.displayName) link") }
                    ?? String(localized: "link only"))
        case .noSource: Note(text: String(localized: "no source"))
        case .fileMissing: Note(text: String(localized: "file missing"), isDanger: true)
        case .file, .slice: credit.map { Note(text: $0, isCredit: true) }
        }
    }

    /// Link-only, no-source and missing-file titles are dimmed; no-source is also italic.
    var isDimmed: Bool { [.linkOnly, .noSource, .fileMissing].contains(playState) }
    var isItalic: Bool { playState == .noSource }
}

extension Components.Schemas.PlayState {
    /// The row tooltip and the icon's VoiceOver label (board 5).
    var label: String {
        switch self {
        case .file: String(localized: "Playable from its own file")
        case .slice: String(localized: "Playable from a range of a single-file album")
        case .radioExcerpt: String(localized: "Playable only as an excerpt inside a radio episode")
        case .linkOnly: String(localized: "Link only — opens in browser")
        case .noSource: String(localized: "No source — metadata only")
        case .fileMissing: String(localized: "File missing on disk")
        }
    }

    /// Whether a track in this state plays in the app.
    var plays: Bool { [.file, .slice, .radioExcerpt].contains(self) }
}

extension Components.Schemas.SourceKind {
    var displayName: String {
        switch self {
        case .file: String(localized: "File")
        case .slice: String(localized: "Range")
        case .youtube: "YouTube"
        case .bandcamp: "Bandcamp"
        case .soundcloud: "SoundCloud"
        case .nts: "NTS"
        case .spotify: "Spotify"
        case .torrent: String(localized: "Torrent")
        }
    }
}

extension Components.Schemas.ReleaseGroupType {
    /// User-facing type pill: Album, EP, Single, Compilation, Other.
    var displayName: String {
        switch self {
        case .album: String(localized: "Album")
        case .ep: "EP"
        case .single: String(localized: "Single")
        case .compilation: String(localized: "Compilation")
        case .other: String(localized: "Other")
        }
    }
}

enum LibraryFormat {
    /// "1997" from a partial date "1997[-09[-22]]".
    static func year(_ date: String?) -> String? {
        guard let date, date.count >= 4 else { return nil }
        return String(date.prefix(4))
    }

    /// "4:05", "1:02:03" (tabular in the views).
    static func duration(ms: Int?) -> String? {
        guard let ms else { return nil }
        let seconds = (ms + 500) / 1000
        let (h, m, s) = (seconds / 3600, seconds / 60 % 60, seconds % 60)
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%d:%02d", m, s)
    }

    static func count(_ n: Int) -> String { n.formatted(.number.grouping(.automatic)) }
}
