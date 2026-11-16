import EPAPI
import Foundation
import Observation

/// One entity's note while it is shown: the text being edited, what the server has, and
/// autosave with optimistic concurrency (`PUT /notes` with `base_updated_at`; a 409 is a
/// conflict the user resolves). Edits are saved `debounce` after the last keystroke, and
/// at once when the note is left (`flush`). Nothing is kept on disk.
@Observable
final class NoteDocument {
    enum Status: Equatable {
        /// Nothing edited since it was loaded.
        case idle
        /// Edited, not saved yet ("Editing…").
        case editing
        case saving
        case saved
        /// The save failed; the edit is kept and saved again on the next change or reconnect.
        case failed(String)
        /// Changed elsewhere since the edit started: the server's version.
        case conflict(Components.Schemas.NoteRead)
    }

    let ref: EntityRef
    /// The text as edited here.
    private(set) var text: String
    /// The text as the server has it (after the last load or save).
    private(set) var savedText: String
    private(set) var status = Status.idle
    /// Bumped when `text` is replaced from outside (load, conflict resolution), so the
    /// text view takes it over; edits typed in the view do not bump it.
    private(set) var revision = 0
    /// Link targets as the server resolved them: nil value = broken (target gone).
    private(set) var targets: [LinkKey: LinkTarget?] = [:] {
        didSet { onTargetsChanged?() }
    }

    /// Called after every successful save (other panels may show new backlinks).
    @ObservationIgnored var onSaved: (() -> Void)?
    /// Called when the server told more about link targets (the editor restyles).
    @ObservationIgnored var onTargetsChanged: (() -> Void)?

    /// Documents with edits the server does not have yet, whether or not they are shown
    /// (the note was left while unreachable, in conflict, or the save failed). They stay
    /// until saved, so the text is never dropped: the panel shows the same document again
    /// when its entity comes back, the app waits for them when quitting, and a reconnect
    /// saves them. Nothing is kept on disk.
    private(set) static var unsaved: [ObjectIdentifier: NoteDocument] = [:]

    /// A panel shows this document (not an orphan waiting for its entity to be shown again).
    @ObservationIgnored var isShown = false

    @ObservationIgnored private(set) var baseUpdatedAt: Date?
    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var saveTask: Task<Void, Never>?
    @ObservationIgnored private var debounceTask: Task<Void, Never>?

    init(_ note: Components.Schemas.NoteRead, ref: EntityRef, connection: ServerConnection, debounce: Duration = .seconds(1)) {
        self.ref = ref
        self.connection = connection
        self.debounce = debounce
        text = note.body
        savedText = note.body
        baseUpdatedAt = note.updatedAt
        targets = Self.targets(of: note)
    }

    var hasUnsavedChanges: Bool { text != savedText }

    /// An edited note whose panel went away, for `ref`; taken over by the next panel that shows it.
    static func orphan(for ref: EntityRef, connection: ServerConnection) -> NoteDocument? {
        unsaved.values.first { $0.ref == ref && $0.connection === connection && !$0.isShown }
    }

    /// Saves every edited note now (reconnect, quit).
    static func flushUnsaved() {
        for document in unsaved.values { document.flush() }
    }

    /// Waits until nothing is edited and unsaved any more, or `timeout`; true when all saved.
    static func settleUnsaved(timeout: Duration) async -> Bool {
        flushUnsaved()
        let deadline = ContinuousClock.now + timeout
        while ContinuousClock.now < deadline {
            let pending = unsaved.values
            if pending.isEmpty { return true }
            // A conflict or failure only ends with the user's decision: no use waiting.
            if pending.allSatisfy({ $0.needsUser }) { return false }
            try? await Task.sleep(for: .milliseconds(50))
        }
        return unsaved.isEmpty
    }

    /// Unsaved and not going to save by itself (conflict, failed, unreachable).
    var needsUser: Bool {
        guard hasUnsavedChanges else { return false }
        if case .conflict = status { return true }
        if case .failed = status { return true }
        return !connection.isConnected
    }

    private func track() {
        if hasUnsavedChanges {
            Self.unsaved[ObjectIdentifier(self)] = self
        } else {
            Self.unsaved[ObjectIdentifier(self)] = nil
        }
    }

    // MARK: - Editing

    /// The user changed the text: saved after the debounce.
    func edit(_ newText: String) {
        guard newText != text else { return }
        text = newText
        track()
        if case .conflict = status { return }
        status = hasUnsavedChanges ? .editing : .idle
        scheduleSave()
    }

    /// Saves now if there is something to save (the note is left, the app reconnects).
    func flush() {
        debounceTask?.cancel()
        save()
    }

    /// A link the user just inserted: its target is known before the next save says so.
    func remember(_ target: LinkTarget, as key: LinkKey) {
        targets[key] = .some(target)
    }

    /// The target as last resolved: `.some(nil)` broken, nil not known yet.
    func target(of key: LinkKey) -> LinkTarget?? { targets[key] }

    // MARK: - Loading

    /// The note as the server has it now (a reload). Taken over only when nothing here is
    /// unsaved; otherwise the next save decides (and conflicts if it changed).
    func apply(_ note: Components.Schemas.NoteRead) {
        // An answer older than what this document saved or loaded (a reload that was read
        // before our own save) would bring back the old text and a stale base.
        if let base = baseUpdatedAt, let updated = note.updatedAt, updated < base { return }
        targets = Self.targets(of: note)
        guard !hasUnsavedChanges, saveTask == nil, status != .saving else { return }
        if case .conflict = status { return }
        baseUpdatedAt = note.updatedAt
        guard note.body != text else { return }
        replace(with: note.body)
    }

    // MARK: - Conflicts

    /// Keep this version: save it over the server's.
    func keepMine() {
        guard case .conflict(let current) = status else { return }
        baseUpdatedAt = current.updatedAt
        savedText = current.body
        track()
        guard hasUnsavedChanges else {
            status = .idle
            return
        }
        status = .editing
        save()
    }

    /// Take the server's version; the edit here is dropped.
    func takeTheirs() {
        guard case .conflict(let current) = status else { return }
        baseUpdatedAt = current.updatedAt
        targets = Self.targets(of: current)
        status = .idle
        replace(with: current.body)
    }

    /// Waits for the debounce and the save in flight (tests).
    func settle() async {
        await debounceTask?.value
        await saveTask?.value
    }

    // MARK: - Saving

    private func scheduleSave() {
        debounceTask?.cancel()
        guard hasUnsavedChanges else { return }
        let debounce = debounce
        debounceTask = Task {
            try? await Task.sleep(for: debounce)
            guard !Task.isCancelled else { return }
            save()
        }
    }

    private func save() {
        guard saveTask == nil else { return }  // the save in flight saves again when done
        if case .conflict = status { return }
        guard hasUnsavedChanges, connection.isConnected else { return }
        let body = text
        let base = baseUpdatedAt
        let client = connection.client
        let ref = ref
        status = .saving
        saveTask = Task {
            let outcome: Status
            do {
                let output = try await client.saveNote(
                    path: .init(_type: ref.type, id: ref.id),
                    body: .json(.init(baseUpdatedAt: base, body: body)))
                switch output {
                case .ok(let ok):
                    let note = try ok.body.json
                    savedText = note.body
                    baseUpdatedAt = note.updatedAt
                    targets.merge(Self.targets(of: note)) { _, new in new }
                    outcome = .saved
                    onSaved?()
                case .conflict(let conflict):
                    outcome = .conflict(try conflict.body.json.current)
                case .notFound:
                    outcome = .failed(String(localized: "Not saved: this was deleted."))
                default:
                    outcome = .failed(String(localized: "Not saved."))
                }
            } catch {
                connection.reportFailure(error)
                outcome = .failed(String(localized: "Not saved — server unreachable."))
            }
            saveTask = nil
            track()
            switch outcome {
            case .saved where hasUnsavedChanges:
                status = .editing
                scheduleSave()
            default:
                status = outcome
            }
        }
    }

    private func replace(with body: String) {
        debounceTask?.cancel()
        text = body
        savedText = body
        revision += 1
        track()
    }

    private static func targets(of note: Components.Schemas.NoteRead) -> [LinkKey: LinkTarget?] {
        var targets: [LinkKey: LinkTarget?] = [:]
        for link in note.links {
            targets[LinkKey(type: link.targetType, ref: link.ref)] = .some(link.target.map(LinkTarget.init))
        }
        return targets
    }
}

/// What a link points to, as far as the editor needs it: where to open and what to call it.
struct LinkTarget: Equatable {
    var ref: EntityRef
    var title: String

    init(ref: EntityRef, title: String) {
        self.ref = ref
        self.title = title
    }

    init(_ entity: Components.Schemas.EntityRead) {
        self.init(ref: EntityRef(type: entity._type, id: entity.id), title: entity.title)
    }
}
