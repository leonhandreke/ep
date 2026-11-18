import Foundation
import Observation

/// Where a main window is: the view it shows and where each view is. One entry of the
/// window's navigation history.
nonisolated struct WindowLocation: NavigationLocation {
    var section = AppSection.library
    var library = LibraryLocation()
    var radio = RadioLocation()
}

/// A window's one navigation history, across the Library and the Radio view (decision 13:
/// each window has its own). Selections in either view, links, search results, Show
/// playing and switching views all record here, so Back walks what the window really
/// showed: from an album opened by a tracklist link, Back returns to the Radio view at
/// that entry. The browsers read their location from here (`LibraryBrowser.location`,
/// `RadioBrowser.location`) and record changes through `go(to:column:change:)`.
@Observable
final class WindowNavigation: HistoryNavigating {
    private(set) var history = NavigationHistory<WindowLocation>()
    var location: WindowLocation { history.current }

    /// The views whose content follows the location (set by the browsers).
    @ObservationIgnored weak var library: LibraryBrowser?
    @ObservationIgnored weak var radio: RadioBrowser?
    @ObservationIgnored private let clock: () -> ContinuousClock.Instant

    init(clock: @escaping () -> ContinuousClock.Instant = { .now }) {
        self.clock = clock
    }

    // MARK: - Recording

    /// Records a new location in `section`: the current one changed by `change`. A change
    /// in another view than the one shown is never coalesced with the last.
    func go(to section: AppSection, column: NavigationColumn, change: (inout WindowLocation) -> Void) {
        var target = location
        target.section = section
        change(&target)
        history.push(target, column: section == location.section ? column : .jump, at: clock())
    }

    /// Changes the current entry in place (selecting a track or a tracklist entry).
    func replaceCurrent(_ change: (inout WindowLocation) -> Void) {
        var target = location
        change(&target)
        history.replaceCurrent(target)
    }

    /// Switches the window's view (toolbar, ⌘1 / ⌘2): a history entry of its own.
    func show(section: AppSection) {
        guard section != location.section else { return }
        go(to: section, column: .jump) { _ in }
    }

    /// The view a restored window was showing: not a navigation.
    func restore(section: AppSection) {
        replaceCurrent { $0.section = section }
    }

    // MARK: - Back and forward

    var canGoBack: Bool { history.canGoBack }
    var canGoForward: Bool { history.canGoForward }

    func goBack() {
        let before = location
        guard history.back() != nil else { return }
        showChanges(from: before)
    }

    func goForward() {
        let before = location
        guard history.forward() != nil else { return }
        showChanges(from: before)
    }

    /// Both views load what changed between `before` and the current location.
    private func showChanges(from before: WindowLocation) {
        library?.showChanges(from: before.library)
        radio?.showChanges(from: before.radio)
    }
}
