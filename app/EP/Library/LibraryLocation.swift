import EPAPI
import Foundation

/// What the release-groups column has selected: an album, or the artist's loose tracks
/// (recordings on no release group).
nonisolated enum AlbumSelection: Hashable {
    case releaseGroup(String)
    case looseTracks

    var releaseGroupID: String? {
        if case .releaseGroup(let id) = self { id } else { nil }
    }
}

/// Where the Library view is: the selected artist → album → track, left to right. One
/// entry of the navigation history. `trackID` is a track's ID in an album and a
/// recording's ID among loose tracks (the rows are recordings there).
nonisolated struct LibraryLocation: Hashable {
    var artistID: String?
    var album: AlbumSelection?
    var trackID: String?
}

/// Something `LibraryBrowser.navigate(to:)` can show. Later stages (note links, search
/// results, "Show playing") navigate through it.
nonisolated enum LibraryEntity: Hashable, Codable, Sendable {
    case artist(String)
    case releaseGroup(String)
    case track(String)
    case recording(String)
}

/// A place a window's view can be: one entry of its navigation history. A new history
/// starts at the empty location (nothing selected).
nonisolated protocol NavigationLocation: Hashable {
    init()
}

extension LibraryLocation: NavigationLocation {}

/// The column a navigation changed, for coalescing: the first, second or third list
/// column (Library: artist, album, track; Radio: show, episode, entry), or a jump
/// (links, back/forward, search) that is never coalesced.
enum NavigationColumn {
    case first, second, third, jump

    static let artist = first, album = second, track = third
}

/// A view with a navigation history: what the toolbar's Back / Forward and ⌘[ / ⌘] act
/// on (the Library's or the Radio view's, whichever the window shows).
protocol HistoryNavigating: AnyObject {
    var canGoBack: Bool { get }
    var canGoForward: Bool { get }
    func goBack()
    func goForward()
}

/// Back/forward through the locations a window's view has shown (⌘[ / ⌘]). Holding an
/// arrow key in one column would otherwise fill it with every row passed: a change in the
/// same column as the previous one, within `coalescing`, replaces that entry instead.
/// The Library and the Radio view each have one.
struct NavigationHistory<Location: NavigationLocation> {
    typealias Column = NavigationColumn

    private(set) var entries: [Location] = [Location()]
    private(set) var index = 0
    var limit = 200
    var coalescing: Duration = .milliseconds(800)

    private var lastPush: (column: Column, at: ContinuousClock.Instant)?

    var current: Location { entries[index] }
    var canGoBack: Bool { index > 0 }
    var canGoForward: Bool { index < entries.count - 1 }

    /// Records a new location: forward entries are dropped.
    mutating func push(_ location: Location, column: Column, at now: ContinuousClock.Instant = .now) {
        guard location != current else { return }
        if column != .jump, let lastPush, lastPush.column == column, now - lastPush.at < coalescing, index > 0 {
            entries[index] = location
            entries.removeSubrange((index + 1)...)
        } else {
            entries.removeSubrange((index + 1)...)
            entries.append(location)
            if entries.count > limit { entries.removeFirst(entries.count - limit) }
            index = entries.count - 1
        }
        self.lastPush = (column, now)
    }

    /// Changes the current entry in place (selecting a track is not a navigation).
    mutating func replaceCurrent(_ location: Location) {
        entries[index] = location
    }

    mutating func back() -> Location? {
        guard canGoBack else { return nil }
        index -= 1
        lastPush = nil
        return current
    }

    mutating func forward() -> Location? {
        guard canGoForward else { return nil }
        index += 1
        lastPush = nil
        return current
    }
}

extension Components.Schemas.ArtistCreditRead {
    /// The artist to show a credited thing under: `preferred` if the credit names it,
    /// else the first credited artist.
    func artistID(preferring preferred: String?) -> String? {
        if let preferred, names.contains(where: { $0.artistId == preferred }) { return preferred }
        return names.first?.artistId
    }
}
