import EPAPI
import SwiftUI

/// Opens an entity from a note link, a backlink or a radio appearance: in this window
/// (Library: `LibraryBrowser.navigate(to:)`, recorded in its history; radio shows and
/// episodes: the Radio view, stage 7) or in a new main window (⌥⌘-click, decision 13).
struct OpenEntityAction {
    let handler: @MainActor (_ target: OpenTarget, _ newWindow: Bool) -> Void

    @MainActor func callAsFunction(_ target: OpenTarget, newWindow: Bool = false) {
        handler(target, newWindow)
    }
}

/// What `OpenEntityAction` opens: an entity, or a radio tracklist entry in its episode.
nonisolated struct OpenTarget: Hashable, Codable, Sendable {
    var entity: EntityRef
    /// A tracklist entry of `entity` (an episode).
    var radioEntryID: String?
}

extension EnvironmentValues {
    /// Set by the main window.
    @Entry var openEntity: OpenEntityAction?
    /// A click on a tag chip (decision 7: the search palette filtered to that tag). The
    /// one hook stage 8's search palette implements; nil until then.
    @Entry var showTag: ((Components.Schemas.TagRead) -> Void)?
}
