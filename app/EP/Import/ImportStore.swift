import EPAPI
import Foundation
import Observation

/// The import queue for the whole app (design §7): the toolbar button's state from
/// `GET /imports/summary`, the job list, logs, the job actions, and the toast when a job
/// finishes or needs review.
///
/// No push from the server: the summary is polled only while a job is queued or running,
/// and polling stops when the queue is idle. A refresh (connected, window active, a job
/// started or answered) polls once and goes on only while something runs. The job list
/// is reloaded when the summary changes.
@Observable
final class ImportStore {
    typealias Job = Components.Schemas.ImportJobSummary
    typealias Summary = Components.Schemas.ImportsSummaryRead

    /// The toolbar button's badge (board 11).
    enum Badge: Equatable {
        case none
        /// Jobs waiting for a decision: a count in `--warn`.
        case review(Int)
        /// A failed job: "!" in `--danger`, until it is retried or dismissed.
        case failed
    }

    /// "Late Night Tapes is ready for review", "Treasure is imported", "Shade failed".
    struct Toast: Identifiable, Equatable {
        enum Kind { case done, review, failed }
        let id = UUID()
        var jobID: String
        var kind: Kind
        var title: String
        var message: String
        var coverURL: String?
    }

    private(set) var summary: Summary?
    /// Jobs, newest first (dismissed ones left out).
    private(set) var jobs: [Job] = []
    /// Logs of the jobs whose log is shown (the running job's card, expanded rows).
    private(set) var logs: [String: [Components.Schemas.LogEntry]] = [:]
    /// The answered issues of those jobs: a job queued again after review shows what was
    /// answered (the server sets `Issue.answer` when the answers are sent).
    private(set) var answered: [String: [Components.Schemas.Issue]] = [:]
    /// Rows whose log is expanded.
    private(set) var expanded: Set<String> = []
    private(set) var toast: Toast?
    /// Bumped when a job has put something into the library: windows reload what they show.
    private(set) var libraryChanges = 0
    /// What the last job action could not do (a 409: the job moved on meanwhile).
    private(set) var actionError: String?
    private(set) var isPolling = false

    /// How many import sheets show the queue: logs are loaded only while one does.
    @ObservationIgnored private var queueViewers = 0
    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let interval: Duration
    @ObservationIgnored private let toastDuration: Duration
    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var toastTask: Task<Void, Never>?
    /// Whether `jobs` was loaded once: a job's first sighting is not a change.
    @ObservationIgnored private var jobsLoaded = false
    /// `refresh()` was called while a poll was in flight.
    @ObservationIgnored private var pollAgain = false

    static let states: [Components.Schemas.KnownJobState] = [
        .queued, .running, .applying, .needsReview, .done, .failed,
    ]
    static let pageSize = 100

    init(connection: ServerConnection, interval: Duration = .seconds(2), toastDuration: Duration = .seconds(8)) {
        self.connection = connection
        self.interval = interval
        self.toastDuration = toastDuration
    }

    // MARK: - What the toolbar shows

    /// Whether a job is waiting to run or running: polling goes on while it is.
    var isActive: Bool { (summary?.queued ?? 0) + (summary?.running ?? 0) > 0 }

    var badge: Badge {
        guard let summary else { return .none }
        if summary.failed > 0 { return .failed }
        return summary.needsReview > 0 ? .review(summary.needsReview) : .none
    }

    /// The progress ring: how far the running job is (0 while it has no measure yet);
    /// nil when nothing runs.
    var progress: Double? {
        guard let summary, summary.running > 0 else { return nil }
        return summary.runningFraction ?? 0
    }

    /// "Import — 1 running, 1 waiting for review" (the button's VoiceOver label).
    var accessibilitySummary: String {
        guard let summary else { return String(localized: "Import") }
        var parts: [String] = []
        if summary.running > 0 { parts.append(String(localized: "\(summary.running) running")) }
        if summary.queued > 0 { parts.append(String(localized: "\(summary.queued) queued")) }
        if summary.needsReview > 0 { parts.append(String(localized: "\(summary.needsReview) waiting for review")) }
        if summary.failed > 0 { parts.append(String(localized: "\(summary.failed) failed")) }
        return parts.isEmpty
            ? String(localized: "Import") : String(localized: "Import — \(parts.joined(separator: ", "))")
    }

    /// The job ⇧⌘I and the toast's Review open: the newest that waits for a decision.
    var firstToReview: Job? { jobs.first { $0.actions.contains(.review) } }

    func job(id: String) -> Job? { jobs.first { $0.id == id } }

    // MARK: - Polling

    /// Polls once now, and on while a job is queued or running. One loop for the whole
    /// app: a call while it runs does not start a second, but makes sure a poll that
    /// began earlier is followed by another (a job started meanwhile is not missed).
    func refresh() {
        guard pollTask == nil else {
            pollAgain = true
            return
        }
        isPolling = true
        pollTask = Task { [weak self] in
            while !Task.isCancelled, let self {
                pollAgain = false
                let wait = await poll()
                if pollAgain, !Task.isCancelled { continue }
                guard let wait else { break }
                try? await Task.sleep(for: wait)
            }
            self?.pollTask = nil
            self?.isPolling = false
        }
    }

    /// One poll; the time to the next while something runs, nil to stop. Polling also
    /// stops while the server is unreachable: the connection's own retry takes over, and
    /// the window's refresh on reconnecting starts it again.
    private func poll() async -> Duration? {
        await load()
        return isActive && connection.isConnected && !Task.isCancelled ? interval : nil
    }

    /// Waits until polling has stopped (tests).
    func settle() async { await pollTask?.value }

    /// Stops polling (a different server; tests).
    func stop() {
        pollTask?.cancel()
        pollTask = nil
        isPolling = false
    }

    /// An import sheet shows the queue (or stops showing it): logs follow it.
    func queueShown(_ shown: Bool) {
        queueViewers = max(0, queueViewers + (shown ? 1 : -1))
        if shown {
            actionError = nil
            Task { await loadJobs() }
            refresh()
        }
    }

    private func load() async {
        do {
            let summary = try await connection.client.getImportsSummary().ok.body.json
            let changed = summary != self.summary
            self.summary = summary
            if changed || !jobsLoaded || queueViewers > 0 { await loadJobs() }
        } catch {
            guard !ServerConnection.isCancellation(error) else { return }
            connection.reportFailure(error)
        }
    }

    /// Reloads the job list (and the logs shown) and notices which jobs moved on.
    func loadJobs() async {
        do {
            let ok = try await connection.client.listImportJobs(
                query: .init(state: Self.states, limit: Self.pageSize)
            ).ok
            let jobs = try ok.body.json
            notice(changesFrom: self.jobs, to: jobs)
            self.jobs = jobs
            jobsLoaded = true
            if queueViewers > 0 { await loadLogs() }
        } catch {
            guard !ServerConnection.isCancellation(error) else { return }
            connection.reportFailure(error)
        }
    }

    /// Logs of the running jobs (their card) and of the expanded rows.
    private func loadLogs() async {
        let ids = jobs.filter { Self.isRunning($0.state) || expanded.contains($0.id) }.map(\.id)
        for id in ids { await loadLog(id) }
    }

    private func loadLog(_ id: String) async {
        guard let job = try? await connection.client.getImportJob(path: .init(id: id)).ok.body.json else { return }
        keep(job)
    }

    private func keep(_ job: Components.Schemas.ImportJobRead) {
        logs[job.id] = job.log
        answered[job.id] = (job.proposal?.issues ?? []).filter { $0.answer != nil }
    }

    /// A job that reached `done`, `needs_review` or `failed` since the last look gets a
    /// toast; one that put something into the library makes the windows reload.
    private func notice(changesFrom old: [Job], to new: [Job]) {
        guard jobsLoaded else { return }
        let before = Dictionary(old.map { ($0.id, $0.state) }, uniquingKeysWith: { first, _ in first })
        var library = false
        for job in new.reversed() {
            let previous = before[job.id]
            guard previous != job.state else { continue }
            let wasActive = previous.map(Self.isActive) ?? true
            switch job.state.known {
            case .done:
                library = library || (wasActive && job.createdAnything)
                show(Toast(job: job, kind: .done))
            case .needsReview:
                // A job left in review may have applied the rest (non-blocking issues).
                library = library || (wasActive && job.createdAnything)
                show(Toast(job: job, kind: .review))
            case .failed:
                show(Toast(job: job, kind: .failed))
            default:
                break
            }
        }
        if library { libraryChanges += 1 }
    }

    static func isActive(_ state: Components.Schemas.JobState) -> Bool {
        state == .queued || isRunning(state)
    }

    static func isRunning(_ state: Components.Schemas.JobState) -> Bool {
        state == .running || state == .applying
    }

    // MARK: - Toast

    func show(_ toast: Toast) {
        self.toast = toast
        toastTask?.cancel()
        let duration = toastDuration
        toastTask = Task { [weak self] in
            try? await Task.sleep(for: duration)
            guard !Task.isCancelled, self?.toast?.id == toast.id else { return }
            self?.toast = nil
        }
    }

    func dismissToast() {
        toastTask?.cancel()
        toast = nil
    }

    // MARK: - Logs

    func toggleLog(_ id: String) {
        if expanded.remove(id) == nil {
            expanded.insert(id)
            Task { await loadLog(id) }
        }
    }

    // MARK: - Starting and acting on jobs

    /// A job the import sheet just started (or a review just re-queued): listed at once,
    /// and polled until it is done.
    func started(_ job: Components.Schemas.ImportJobRead) {
        jobs.removeAll { $0.id == job.id }
        jobs.insert(Job(job), at: 0)
        keep(job)
        refresh()
    }

    enum Action {
        case retry, dismiss, cancel
    }

    /// Retry (the whole job, failed downloads and reads again), Dismiss (out of the
    /// inbox), Cancel (a queued or running job).
    func perform(_ action: Action, on id: String) async {
        actionError = nil
        let client = connection.client
        do {
            let job: Components.Schemas.ImportJobRead
            switch action {
            case .retry:
                switch try await client.retryImportJob(path: .init(id: id)) {
                case .ok(let ok): job = try ok.body.json
                case .conflict(let conflict): return failed(try conflict.body.json.detail)
                default: return failed(nil)
                }
            case .dismiss:
                switch try await client.dismissImportJob(path: .init(id: id)) {
                case .ok(let ok): job = try ok.body.json
                case .conflict(let conflict): return failed(try conflict.body.json.detail)
                default: return failed(nil)
                }
            case .cancel:
                switch try await client.cancelImportJob(path: .init(id: id)) {
                case .ok(let ok): job = try ok.body.json
                case .conflict(let conflict): return failed(try conflict.body.json.detail)
                default: return failed(nil)
                }
            }
            if job.state == .dismissed {
                jobs.removeAll { $0.id == id }
                expanded.remove(id)
                logs[id] = nil
                answered[id] = nil
            } else if let index = jobs.firstIndex(where: { $0.id == id }) {
                jobs[index] = Job(job)
            }
        } catch {
            guard !ServerConnection.isCancellation(error) else { return }
            connection.reportFailure(error)
            failed(nil)
        }
        refresh()
    }

    private func failed(_ detail: String?) {
        actionError =
            detail.map { String(localized: "Couldn’t do that: \($0).") }
            ?? String(localized: "Couldn’t do that. Try again.")
        refresh()
    }
}

extension ImportStore.Toast {
    init(job: ImportStore.Job, kind: Kind) {
        let title = job.displayTitle
        jobID = job.id
        self.kind = kind
        coverURL = job.coverUrl
        switch kind {
        case .done:
            self.title = String(localized: "\(title) is imported")
            message = job.doneDetail
        case .review:
            self.title = String(localized: "\(title) is ready for review")
            message =
                job.openIssues == 1
                ? String(localized: "1 question needs a decision")
                : String(localized: "\(job.openIssues) questions need a decision")
        case .failed:
            self.title = String(localized: "\(title) could not be imported")
            message = job.error ?? job.firstIssue?.message ?? ""
        }
    }
}

extension Components.Schemas.ImportJobSummary {
    /// The summary part of a full job read (the answer to a start, review or action).
    init(_ job: Components.Schemas.ImportJobRead) {
        self.init(
            actions: job.actions, counts: job.counts, coverUrl: job.coverUrl, created: job.created,
            createdAt: job.createdAt, error: job.error, existing: job.existing, firstIssue: job.firstIssue, id: job.id,
            input: job.input, inputHostPath: job.inputHostPath, kind: job.kind, linkKind: job.linkKind,
            openIssues: job.openIssues, progress: job.progress, state: job.state, subtitle: job.subtitle,
            title: job.title, updatedAt: job.updatedAt, waitingFor: job.waitingFor, watchDirId: job.watchDirId)
    }
}
