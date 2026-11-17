import EPAPI
import Foundation

/// What job rows, the active job card, the toast and the review sheet say about a job.
extension Components.Schemas.ImportJobSummary {
    /// "Fennesz — Agora", else the input (a link, or the folder's name).
    var displayTitle: String {
        if let title {
            return subtitle.map { "\($0) — \(title)" } ?? title
        }
        if kind == .folder {
            return URL(fileURLWithPath: inputHostPath ?? input).lastPathComponent
        }
        return ImportFormat.shortLink(input)
    }

    /// "Bandcamp album", "Watched folder · ~/Downloads/Music", "Folder".
    var sourceLabel: String {
        if let linkKind { return linkKind.displayName }
        guard kind == .folder else { return kind.displayName }
        if watchDirId != nil {
            let parent = URL(fileURLWithPath: inputHostPath ?? input).deletingLastPathComponent().path
            return String(localized: "Watched folder · \(ImportFormat.abbreviatingHome(parent))")
        }
        return String(localized: "Folder")
    }

    /// "10 tracks · 1 new artist" (what the last apply created).
    var doneDetail: String {
        let tracks = created.tracks
        guard tracks + created.recordings + created.artists + created.releaseGroups > 0 else {
            return String(localized: "Nothing new")
        }
        var parts = [tracks == 1 ? String(localized: "1 track") : String(localized: "\(tracks) tracks")]
        if created.artists > 0 {
            parts.append(
                created.artists == 1
                    ? String(localized: "1 new artist") : String(localized: "\(created.artists) new artists"))
        }
        return parts.joined(separator: " · ")
    }

    /// Whether every open issue is a failure a retry tries again (downloads, reads).
    var onlyFailures: Bool { counts.failed > 0 && counts.failed == openIssues }

    /// Whether the job put anything into the library on its last apply.
    var createdAnything: Bool {
        created.artists + created.releaseGroups + created.recordings + created.tracks + created.sources > 0
    }
}

extension Components.Schemas.LinkKind {
    var displayName: String {
        switch self {
        case .bandcampAlbum: String(localized: "Bandcamp album")
        case .bandcampTrack: String(localized: "Bandcamp track")
        case .youtubeVideo: String(localized: "YouTube video")
        case .youtubePlaylist: String(localized: "YouTube playlist")
        case .ntsEpisode: String(localized: "NTS episode")
        case .soundcloud: "SoundCloud"
        case .spotify: "Spotify"
        }
    }
}

extension Components.Schemas.ImportKind {
    var displayName: String {
        switch self {
        case .folder: String(localized: "Folder")
        case .bandcamp: "Bandcamp"
        case .youtube: "YouTube"
        case .nts: "NTS"
        }
    }
}

extension Components.Schemas.ImportStep {
    /// The four segments of the active job's progress strip (design 7.1).
    var title: String {
        switch self {
        case .metadata: String(localized: "Fetching metadata")
        case .matching: String(localized: "Matching")
        case .downloading: String(localized: "Downloading")
        case .applying: String(localized: "Applying")
        }
    }
}

enum ImportFormat {
    /// "youtube.com/playlist?list=PL…" for a long link.
    static func shortLink(_ link: String) -> String {
        var text = link
        for prefix in ["https://", "http://", "www."] where text.hasPrefix(prefix) {
            text.removeFirst(prefix.count)
        }
        return text.count > 48 ? String(text.prefix(47)) + "…" : text
    }

    static func abbreviatingHome(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix(home + "/") || path == home ? "~" + path.dropFirst(home.count) : path
    }

    /// "Today at 22:30", "Yesterday at 21:02" (the system's relative words, in the user's
    /// language: "Heute, 22:30"), else "3 Nov".
    static func time(_ date: Date, now: Date = .now, calendar: Calendar = .autoupdatingCurrent) -> String {
        guard let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
            calendar.isDate(date, inSameDayAs: now) || calendar.isDate(date, inSameDayAs: yesterday)
        else {
            return date.formatted(.dateTime.day().month(.abbreviated))
        }
        return relativeFormatter.string(from: date)
    }

    /// Relative day words ("Today", "Yesterday") with the time.
    private static let relativeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = .autoupdatingCurrent
        formatter.doesRelativeDateFormatting = true
        formatter.dateStyle = .medium
        formatter.timeStyle = .short
        return formatter
    }()

    /// "22:41:07" (log lines).
    static func clock(_ date: Date) -> String {
        date.formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits).second(.twoDigits))
    }

    /// "10:02", "1:12:40" (a position in an episode).
    static func position(ms: Int) -> String {
        let seconds = ms / 1000
        let (h, m, s) = (seconds / 3600, seconds / 60 % 60, seconds % 60)
        return h > 0
            ? "\(h):\(String(format: "%02d", m)):\(String(format: "%02d", s))" : "\(m):\(String(format: "%02d", s))"
    }

    /// A match score (0–100) as the design writes it: "0.82".
    static func score(_ score: Double) -> String {
        (score / 100).formatted(.number.precision(.fractionLength(2)))
    }
}
