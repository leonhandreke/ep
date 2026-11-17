import EPAPI
import Foundation
import Observation

/// The review sheet (design 7.2, decision 10): every issue of a job as a question row,
/// whatever its kind, with the options its `choices` and `candidates` give; the default
/// preselected, so only the uncertain rows need attention. Keyboard: ↑↓ question, 1–9
/// candidate, N new, U leave out, K keep as entered, R try again, ↩ accept & next,
/// ⌘↩ confirm all.
///
/// Kinds the app has words for get their question; any other kind (one the backend adds
/// later, decoded as an open enum with `known == nil`) shows its message and its choices,
/// so the sheet never depends on the list. A choice this app does not know is not offered.
@Observable
final class ReviewModel {
    typealias Issue = Components.Schemas.Issue
    /// The choices this app knows; a choice a newer server adds is not offered.
    typealias Choice = Components.Schemas.KnownChoice

    enum Filter: Hashable { case uncertain, all }

    enum Phase: Equatable {
        case loading
        case failed(String)
        case ready
    }

    /// One answer a question offers: a candidate, or a choice without one.
    struct Option: Identifiable, Hashable {
        var choice: Choice
        var candidateID: String?
        var title: String
        var detail: String?
        var score: Double?
        /// The key that picks it: "1"–"9", "N", "U", "K", "R".
        var key: String?
        var id: String { "\(choice.rawValue):\(candidateID ?? "")" }
    }

    /// A question row (an issue), or under "All" an item that needed no question.
    struct Row: Identifiable, Equatable {
        var id: String
        var issue: Issue?
        /// Position in an episode ("10:02"), for radio entries.
        var time: String?
        var timeIsEstimate = false
        /// "Alice Coltrane — Turiya and Ramakrishna".
        var title: String
        /// "Same recording as library track?", or what became of an item without a question.
        var question: String
        var options: [Option] = []
        /// What the item is matched to, for one without a question.
        var outcome: String?
        /// "ENTRY 4 OF 31 · 10:02".
        var eyebrow: String
        /// The incoming side of the comparison: label → value.
        var incoming: [(String, String)] = []
        /// The incoming track's audio, for Space (preview), once its file is there.
        var preview: Preview?
        var isOpen: Bool { issue != nil && issue?.answer == nil }

        static func == (lhs: Row, rhs: Row) -> Bool {
            lhs.id == rhs.id && lhs.issue == rhs.issue && lhs.options == rhs.options && lhs.preview == rhs.preview
        }
    }

    /// Where an incoming track's audio is: `GET /imports/{id}/stream?track_key=` serves its
    /// medium's whole file; the track is `startMS..<endMS` of it.
    struct Preview: Equatable {
        var trackKey: String
        var mediaType: String
        var startMS: Int
        var endMS: Int?
    }

    private(set) var phase = Phase.loading
    private(set) var job: Components.Schemas.ImportJobRead?
    private(set) var rows: [Row] = []
    var filter = Filter.uncertain {
        didSet { keepSelectionVisible() }
    }
    private(set) var selectedID: String?
    /// The option chosen per issue (defaults until changed).
    private(set) var picks: [String: Option.ID] = [:]
    /// Rows accepted with ↩ (shown as done; Confirm sends everything anyway).
    private(set) var accepted: Set<String> = []
    private(set) var isSubmitting = false
    private(set) var error: String?

    let jobID: String
    @ObservationIgnored private let connection: ServerConnection

    init(jobID: String, connection: ServerConnection) {
        self.jobID = jobID
        self.connection = connection
    }

    // MARK: - Loading

    func load() async {
        phase = .loading
        do {
            switch try await connection.client.getImportJob(path: .init(id: jobID)) {
            case .ok(let ok): show(try ok.body.json)
            case .notFound: phase = .failed(String(localized: "This import is gone."))
            default: phase = .failed(String(localized: "This import couldn’t be loaded."))
            }
        } catch {
            connection.reportFailure(error)
            phase = .failed(String(localized: "This import couldn’t be loaded."))
        }
    }

    /// Builds the rows from a job read (also for tests).
    func show(_ job: Components.Schemas.ImportJobRead) {
        self.job = job
        rows = Self.rows(for: job)
        picks = [:]
        for row in rows {
            guard let issue = row.issue, let pick = Self.defaultOption(for: issue, in: row.options) else { continue }
            picks[issue.id] = pick.id
        }
        phase = .ready
        if selectedID == nil || !rows.contains(where: { $0.id == selectedID }) {
            selectedID = (rows.first(where: \.isOpen) ?? rows.first)?.id
        }
        if !rows.contains(where: \.isOpen) { filter = .all }
        keepSelectionVisible()
    }

    var openCount: Int { rows.filter(\.isOpen).count }

    var visibleRows: [Row] {
        filter == .all ? rows : rows.filter(\.isOpen)
    }

    var selected: Row? { rows.first { $0.id == selectedID } }

    func pick(for row: Row) -> Option? {
        guard let issue = row.issue, let id = picks[issue.id] else { return nil }
        return row.options.first { $0.id == id }
    }

    /// "31 entries · 26 matched confidently · 5 need a decision".
    var headline: (items: String, decisions: String?) {
        guard let job else { return ("", nil) }
        let proposal = job.proposal
        let items: String
        if let radio = proposal?.radio {
            let tracks = Self.tracks(of: proposal)
            let questioned = Set(rows.compactMap { $0.issue?.subject.key })
            let confident = tracks.filter {
                !questioned.contains($0.key) && Self.isMatched($0.target)
            }.count
            items = String(
                localized: "\(radio.entries.count) entries · \(confident) matched confidently")
        } else {
            let count = Self.tracks(of: proposal).count
            items = count == 1 ? String(localized: "1 track") : String(localized: "\(count) tracks")
        }
        let open = openCount
        guard open > 0 else { return (items, nil) }
        return (
            items, open == 1 ? String(localized: "1 needs a decision") : String(localized: "\(open) need a decision")
        )
    }

    // MARK: - Keyboard and choosing

    func select(_ id: String) { selectedID = id }

    /// ↑ / ↓ through the rows shown.
    func move(_ offset: Int) {
        let shown = visibleRows
        guard !shown.isEmpty else { return }
        let index = shown.firstIndex { $0.id == selectedID } ?? (offset > 0 ? -1 : shown.count)
        selectedID = shown[min(max(index + offset, 0), shown.count - 1)].id
    }

    func choose(_ option: Option, in row: Row) {
        guard let issue = row.issue, row.options.contains(option) else { return }
        picks[issue.id] = option.id
    }

    /// A key typed in the sheet: picks the selected question's option with that key.
    @discardableResult
    func press(_ key: String) -> Bool {
        guard let row = selected, let option = row.options.first(where: { $0.key == key.uppercased() }) else {
            return false
        }
        choose(option, in: row)
        return true
    }

    /// ↩: accepts the selected question's pick and goes to the next open question.
    func acceptAndNext() {
        guard let row = selected else { return }
        if row.issue != nil { accepted.insert(row.id) }
        let shown = visibleRows
        guard let index = shown.firstIndex(where: { $0.id == row.id }) else { return }
        let rest = shown[(index + 1)...] + shown[..<index]
        if let next = rest.first(where: { $0.isOpen && !accepted.contains($0.id) }) ?? shown.dropFirst(index + 1).first
        {
            selectedID = next.id
        }
    }

    /// A library row found with "Search the library for another…": offered as a
    /// candidate of the selected question, and chosen.
    func addCandidate(id: String, title: String, detail: String?) {
        guard let row = selected, let issue = row.issue, let index = rows.firstIndex(where: { $0.id == row.id })
        else { return }
        var option = Option(choice: .pickCandidate, candidateID: id, title: title, detail: detail)
        if let existing = rows[index].options.first(where: { $0.id == option.id }) {
            option = existing
        } else {
            let candidates = rows[index].options.filter { $0.choice == .pickCandidate }.count
            option.key = candidates < 9 ? String(candidates + 1) : nil
            rows[index].options.insert(option, at: candidates)
        }
        picks[issue.id] = option.id
    }

    /// What the selected question's "Search the library for another…" searches: the
    /// type of library row the issue may be answered with (any row of it, not only a
    /// candidate), nil for kinds that take only their candidates.
    var searchType: Components.Schemas.EntityType? {
        guard let issue = selected?.issue, issue.choices.contains(where: { $0 == .pickCandidate }),
            let kind = issue.kind.known
        else { return nil }
        return Self.searchTypes[kind]
    }

    // MARK: - Confirming

    /// The answers Confirm sends: every open question's pick, and any answered question
    /// whose pick was changed.
    var answers: [Components.Schemas.IssueAnswer] {
        rows.compactMap { row in
            guard let issue = row.issue, let pick = pick(for: row) else { return nil }
            if let answer = issue.answer, answer.choice == pick.choice, answer.candidateId == pick.candidateID {
                return nil
            }
            return .init(candidateId: pick.candidateID, choice: .init(pick.choice), issueId: issue.id)
        }
    }

    var canConfirm: Bool { !answers.isEmpty && !isSubmitting }

    /// Sends the answers (`POST /imports/{id}/review`): the job runs again with them.
    /// Returns the job as it is now, nil when nothing was sent or it failed (`error`).
    func confirm() async -> Components.Schemas.ImportJobRead? {
        let answers = answers
        guard !answers.isEmpty else { return nil }
        isSubmitting = true
        error = nil
        defer { isSubmitting = false }
        do {
            switch try await connection.client.reviewImportJob(
                path: .init(id: jobID), body: .json(.init(answers: answers)))
            {
            case .ok(let ok):
                return try ok.body.json
            case .conflict(let conflict):
                error = String(localized: "This import moved on: \((try? conflict.body.json.detail) ?? "").")
            case .unprocessableContent:
                error = String(localized: "Some answers don’t fit any more. Close and review again.")
            case .notFound:
                error = String(localized: "This import is gone.")
            case .undocumented(let status, _):
                error = String(localized: "The server answered \(status).")
            }
        } catch {
            connection.reportFailure(error)
            self.error = String(localized: "The server can’t be reached.")
        }
        return nil
    }

    private func keepSelectionVisible() {
        let shown = visibleRows
        if !shown.contains(where: { $0.id == selectedID }) { selectedID = shown.first?.id }
    }

    // MARK: - Building rows

    /// The default: the stored answer, else the best candidate, else the first choice
    /// (retry for a failure, keep as entered for metadata not found).
    static func defaultOption(for issue: Issue, in options: [Option]) -> Option? {
        if let answer = issue.answer,
            let option = options.first(where: { answer.choice == $0.choice && $0.candidateID == answer.candidateId })
        {
            return option
        }
        return options.first
    }

    /// The stored answer in words: the option it picks ("New recording", "Same recording —
    /// link to …"), a library row picked by search, or the raw choice of one this app does
    /// not know. Nil while the issue is open.
    static func answerTitle(for issue: Issue) -> String? {
        guard let answer = issue.answer else { return nil }
        guard let choice = answer.choice.known else { return answer.choice.rawValue }
        let option = options(for: issue).first { $0.choice == choice && $0.candidateID == answer.candidateId }
        if let option { return option.title }
        return choice == .pickCandidate ? String(localized: "A library item you picked") : title(of: choice, for: issue)
    }

    /// `radioEntry`: the issue is about a radio tracklist entry (skipping leaves it as text).
    static func options(for issue: Issue, radioEntry: Bool = false) -> [Option] {
        var options: [Option] = []
        for choice in issue.choices.compactMap(\.known) {
            if choice == .pickCandidate {
                for (index, candidate) in issue.candidates.enumerated() {
                    options.append(
                        Option(
                            choice: .pickCandidate, candidateID: candidate.id,
                            title: candidateTitle(candidate, first: index == 0, issue: issue),
                            detail: candidate.detail, score: candidate.score, key: index < 9 ? String(index + 1) : nil))
                }
            } else {
                options.append(
                    Option(
                        choice: choice, title: title(of: choice, for: issue, radioEntry: radioEntry),
                        key: key(of: choice)))
            }
        }
        return options
    }

    private static func candidateTitle(_ candidate: Components.Schemas.IssueCandidate, first: Bool, issue: Issue)
        -> String
    {
        let name = [candidate.title, candidate.subtitle].compactMap { $0?.isEmpty == false ? $0 : nil }
            .joined(separator: " — ")
        switch issue.kind.known {
        case .uncertainRecording where first:
            return String(localized: "Same recording — link to “\(name)”")
        case .releaseConflict:
            return String(localized: "Add these files to “\(name)” in the library")
        default:
            return name
        }
    }

    static func key(of choice: Choice) -> String? {
        switch choice {
        case .pickCandidate: nil
        case .createNew: "N"
        case .skip: "U"
        case .keepAsEntered: "K"
        case .retry: "R"
        }
    }

    static func title(of choice: Choice, for issue: Issue, radioEntry: Bool = false) -> String {
        switch choice {
        case .pickCandidate:
            return String(localized: "Use a candidate")
        case .createNew:
            switch issue.subject._type {
            case .artist: return String(localized: "New artist")
            case .release: return String(localized: "New album")
            case .track: return String(localized: "New recording")
            default: return String(localized: "Create new")
            }
        case .skip:
            if radioEntry { return String(localized: "Leave unmatched (plain text)") }
            return String(localized: "Skip — don’t import this")
        case .keepAsEntered:
            return issue.kind == .uncertainMetadata
                ? String(localized: "None — keep as entered") : String(localized: "Keep as entered")
        case .retry:
            return String(localized: "Try again")
        }
    }

    /// Kinds an answer may name any library row of (`PICK_ANY` on the server), and what
    /// to search for it.
    static let searchTypes: [Components.Schemas.KnownIssueKind: Components.Schemas.EntityType] = [
        .uncertainArtist: .artist, .uncertainReleaseGroup: .releaseGroup, .uncertainRecording: .recording,
    ]

    /// The row's short question (12 pt) for the kinds the app has words for; any other
    /// kind shows its message.
    static func shortQuestion(for issue: Issue) -> String {
        let name = issue.subject.label
        switch issue.kind.known {
        case .uncertainRecording: return String(localized: "Same recording as library track?")
        case .uncertainArtist:
            return issue.candidates.isEmpty
                ? String(localized: "Artist not in library") : String(localized: "Which artist “\(name)”?")
        case .uncertainReleaseGroup: return String(localized: "Album already in library?")
        case .uncertainMetadata: return String(localized: "Which MusicBrainz release?")
        case .releaseConflict: return String(localized: "Already in library from another release")
        default: return issue.message
        }
    }

    /// The detail pane's heading (18 pt); nil: the message is the question.
    static func question(for issue: Issue) -> String? {
        let name = issue.subject.label
        switch issue.kind.known {
        case .uncertainRecording: return String(localized: "Is this the same recording as the one in your library?")
        case .uncertainArtist: return String(localized: "Which artist is “\(name)”?")
        case .uncertainReleaseGroup: return String(localized: "Is this album already in your library?")
        case .uncertainMetadata: return String(localized: "Which MusicBrainz release is “\(name)”?")
        case .releaseConflict: return String(localized: "“\(name)” is already in your library from another release.")
        case .downloadFailed: return String(localized: "“\(name)” couldn’t be downloaded.")
        case .fetchFailed: return String(localized: "“\(name)” couldn’t be fetched.")
        case .unreadableFile: return String(localized: "“\(name)” couldn’t be read as audio.")
        default: return nil
        }
    }

    static func rows(for job: Components.Schemas.ImportJobRead) -> [Row] {
        let proposal = job.proposal
        let tracks = tracks(of: proposal)
        let index = Dictionary(tracks.enumerated().map { ($1.key, $0) }, uniquingKeysWith: { first, _ in first })
        let entries = Dictionary(
            (proposal?.radio?.entries ?? []).map { ($0.trackKey, $0) }, uniquingKeysWith: { first, _ in first })
        let radio = proposal?.radio != nil
        let media = Dictionary(
            (proposal?.media ?? []).map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })

        /// A medium's media type is known once its file is there (probed).
        func preview(of track: Components.Schemas.IncomingTrack) -> Preview? {
            guard let key = track.mediumKey, let mediaType = media[key]?.mediaType else { return nil }
            let entry = entries[track.key]
            return Preview(
                trackKey: track.key, mediaType: mediaType, startMS: track.startMs ?? entry?.startMs ?? 0,
                endMS: track.endMs ?? entry?.endMs)
        }

        let source =
            job.linkKind?.displayName ?? (job.kind == .folder ? String(localized: "Folder") : job.kind.displayName)

        func anchor(_ issue: Issue) -> Double {
            let key = issue.subject.key
            switch issue.subject._type {
            case .track: return index[key].map(Double.init) ?? Double(tracks.count)
            case .artist:
                let first = tracks.firstIndex { $0.credit.contains { $0.artistKey == key } }
                return first.map { Double($0) - 0.5 } ?? -1
            case .medium:
                return tracks.firstIndex { $0.mediumKey == key }.map { Double($0) - 0.5 } ?? Double(tracks.count)
            case .release:
                let release = proposal?.releases.first { $0.key == key }
                let first = release?.tracks.first.flatMap { index[$0.key] }
                return first.map { Double($0) - 0.75 } ?? -1
            case .radioEpisode: return -2
            }
        }

        func describe(track: Components.Schemas.IncomingTrack) -> (
            title: String, time: String?, estimate: Bool, eyebrow: String
        ) {
            let credit = track.credit.map { $0.creditedName + $0.joinPhrase }.joined()
            let title = credit.isEmpty ? track.title : "\(credit) — \(track.title)"
            let entry = entries[track.key]
            let position = (index[track.key] ?? 0) + 1
            let time = (entry?.startMs ?? track.startMs).map(ImportFormat.position(ms:))
            let estimate = entry?.offsetIsEstimate ?? false
            let eyebrow =
                radio
                ? String(localized: "Entry \(position) of \(tracks.count)")
                    + (time.map { " · \(estimate ? "~" : "")\($0)" } ?? "")
                : String(localized: "Track \(position) of \(tracks.count)")
            return (title, time, estimate, eyebrow.uppercased())
        }

        func incoming(track: Components.Schemas.IncomingTrack) -> [(String, String)] {
            var facts = [(String(localized: "Title"), track.title)]
            let credit = track.credit.map { $0.creditedName + $0.joinPhrase }.joined()
            if !credit.isEmpty { facts.append((String(localized: "Artist"), credit)) }
            var length = track.lengthMs
            if length == nil, let start = track.startMs, let end = track.endMs { length = end - start }
            if let length, let text = LibraryFormat.duration(ms: length) {
                let estimate = entries[track.key]?.offsetIsEstimate == true
                facts.append((String(localized: "Length"), (estimate ? "~" : "") + text))
            }
            facts.append((String(localized: "From"), radio ? String(localized: "\(source) tracklist") : source))
            return facts
        }

        var rows: [(anchor: Double, order: Int, row: Row)] = []
        let issues = proposal?.issues ?? []
        for (order, issue) in issues.enumerated() {
            let track = issue.subject._type == .track ? tracks.first { $0.key == issue.subject.key } : nil
            var row = Row(
                id: issue.id, issue: issue, title: issue.subject.label, question: shortQuestion(for: issue),
                options: options(for: issue, radioEntry: track != nil && entries[issue.subject.key] != nil),
                eyebrow: eyebrow(for: issue))
            if let track {
                let described = describe(track: track)
                row.title = described.title
                row.time = described.time
                row.timeIsEstimate = described.estimate
                row.eyebrow = described.eyebrow
                row.incoming = incoming(track: track)
                row.preview = preview(of: track)
            } else if issue.subject._type == .artist,
                let artist = proposal?.artists.first(where: { $0.key == issue.subject.key })
            {
                row.incoming = [(String(localized: "Name"), artist.name)]
                if !artist.disambiguation.isEmpty {
                    row.incoming.append((String(localized: "Note"), artist.disambiguation))
                }
                row.incoming.append((String(localized: "From"), source))
            } else if issue.subject._type == .release,
                let release = proposal?.releases.first(where: { $0.key == issue.subject.key })
            {
                row.incoming = [(String(localized: "Title"), release.title)]
                let credit = release.credit.map { $0.creditedName + $0.joinPhrase }.joined()
                if !credit.isEmpty { row.incoming.append((String(localized: "Artist"), credit)) }
                let details = [
                    release.date, release.tracks.isEmpty ? nil : String(localized: "\(release.tracks.count) tracks"),
                ]
                .compactMap { $0 }.joined(separator: " · ")
                if !details.isEmpty { row.incoming.append((String(localized: "Details"), details)) }
                row.incoming.append((String(localized: "From"), source))
            }
            rows.append((anchor(issue), order, row))
        }
        // Items that needed no question (shown under "All").
        let questioned = Set(issues.map(\.subject.key))
        for (position, track) in tracks.enumerated() where !questioned.contains(track.key) {
            let described = describe(track: track)
            let row = Row(
                id: "track:\(track.key)", issue: nil, time: described.time, timeIsEstimate: described.estimate,
                title: described.title, question: outcome(of: track.target), outcome: outcome(of: track.target),
                eyebrow: described.eyebrow, incoming: incoming(track: track), preview: preview(of: track))
            rows.append((Double(position), issues.count + position, row))
        }
        return rows.sorted { ($0.anchor, $0.order) < ($1.anchor, $1.order) }.map(\.row)
    }

    private static func eyebrow(for issue: Issue) -> String {
        switch issue.subject._type {
        case .artist: String(localized: "Artist")
        case .release: String(localized: "Album")
        case .track: String(localized: "Track")
        case .medium: String(localized: "File")
        case .radioEpisode: String(localized: "Episode")
        }
    }

    /// What became of an item without a question: "In library — Jóga", "New", …
    static func outcome(of target: Components.Schemas.Target) -> String {
        switch target {
        case .existing(let existing): String(localized: "In library — \(existing.label)")
        case .musicbrainz(let target):
            target.label.map { String(localized: "From MusicBrainz — \($0)") } ?? String(localized: "From MusicBrainz")
        case .new: String(localized: "New")
        case .skip: String(localized: "Left out")
        case .undecided: String(localized: "Not matched yet")
        }
    }

    static func isMatched(_ target: Components.Schemas.Target) -> Bool {
        switch target {
        case .existing, .musicbrainz: true
        default: false
        }
    }

    /// Every incoming track in order: the radio tracklist's, else the releases' and the
    /// loose ones.
    static func tracks(of proposal: Components.Schemas.Proposal?) -> [Components.Schemas.IncomingTrack] {
        guard let proposal else { return [] }
        let all = proposal.releases.flatMap(\.tracks) + proposal.looseTracks
        guard let radio = proposal.radio else { return all }
        let byKey = Dictionary(all.map { ($0.key, $0) }, uniquingKeysWith: { first, _ in first })
        let ordered = radio.entries.sorted { $0.position < $1.position }.compactMap { byKey[$0.trackKey] }
        let listed = Set(ordered.map(\.key))
        return ordered + all.filter { !listed.contains($0.key) }
    }
}
