import EPAPI
import Foundation
import HTTPTypes
import Synchronization
import Testing

@testable import EP

nonisolated enum ImportFixtures {
    static func summary(queued: Int = 0, running: Int = 0, review: Int = 0, failed: Int = 0, fraction: String = "null")
        -> String
    {
        #"{"queued": \#(queued), "running": \#(running), "needs_review": \#(review), "failed": \#(failed), "running_fraction": \#(fraction), "watching": 2, "changed_at": "2026-11-17T21:00:0\#(queued + running + review + failed).000Z"}"#
    }

    static let created = #"{"artists": 1, "release_groups": 1, "recordings": 10, "tracks": 10, "sources": 10}"#
    static let nothing = #"{"artists": 0, "release_groups": 0, "recordings": 0, "tracks": 0, "sources": 0}"#

    /// A job summary's fields (without `log` and `proposal`).
    static func fields(
        _ id: String, state: String, title: String = "Treasure", actions: [String] = [], created: String = nothing,
        openIssues: Int = 0, error: String? = nil
    ) -> String {
        let actions = actions.map { "\"\($0)\"" }.joined(separator: ",")
        let error = error.map { "\"\($0)\"" } ?? "null"
        return
            #""id": "\#(id)", "created_at": "2026-11-17T20:00:00.000Z", "updated_at": "2026-11-17T20:00:00.000Z", "kind": "folder", "input": "/watch/Treasure", "input_host_path": null, "link_kind": null, "watch_dir_id": null, "state": "\#(state)", "progress": {"stage": null, "step": null, "done": 0, "total": null, "fraction": null}, "error": \#(error), "title": "\#(title)", "subtitle": "Cocteau Twins", "cover_url": null, "first_issue": null, "open_issues": \#(openIssues), "counts": {"tracks": 10, "failed": 0}, "created": \#(created), "existing": null, "waiting_for": null, "actions": [\#(actions)]"#
    }

    static func job(_ id: String, state: String, created: String = nothing, actions: [String] = []) -> String {
        "{\(fields(id, state: state, actions: actions, created: created))}"
    }

    static func read(_ id: String, state: String, proposal: String = "null", actions: [String] = []) -> String {
        #"{\#(fields(id, state: state, actions: actions)), "log": [{"at": "2026-11-17T20:00:01.000Z", "level": "info", "message": "started"}], "proposal": \#(proposal)}"#
    }

    static func track(_ key: String, _ artist: String, _ title: String, start: Int) -> String {
        #"{"key": "\#(key)", "medium_key": "ep", "start_ms": \#(start), "end_ms": null, "disc": 1, "position": 1, "title": "\#(title)", "credit": [{"artist_key": "\#(artist)", "credited_name": "\#(artist)", "join_phrase": ""}], "length_ms": null, "isrcs": [], "external_ids": [], "mbid": null, "mb_track_id": null, "min_length_ms": null, "optional": true, "target": {"kind": "undecided"}}"#
    }

    static func issue(
        _ id: String, kind: String, subject: String, type: String = "track", candidates: [String] = [],
        choices: [String], answer: String = "null", message: String = "A question"
    ) -> String {
        let choices = choices.map { "\"\($0)\"" }.joined(separator: ",")
        return
            #"{"id": "\#(id)", "kind": "\#(kind)", "message": "\#(message)", "subject": {"type": "\#(type)", "key": "\#(subject)", "label": "\#(subject)"}, "candidates": [\#(candidates.joined(separator: ","))], "choices": [\#(choices)], "answer": \#(answer), "blocking": false}"#
    }

    static func candidate(_ id: String, _ title: String, score: Double, type: String = "recording") -> String {
        #"{"type": "\#(type)", "id": "\#(id)", "title": "\#(title)", "subtitle": "Björk", "detail": "4:15", "score": \#(score)}"#
    }

    /// An NTS episode: entry 1 asks "same recording?", entry 2 has metadata not found, the
    /// artist Arca has two candidates (entry 3), entry 4 was answered, entry 5 is matched.
    static let radioProposal: String = {
        let tracks = [
            track("ep#1", "Alice Coltrane", "Turiya", start: 602_000),
            track("ep#2", "Laurel Halo", "Unknown", start: 1_095_000),
            track("ep#3", "Arca", "Piel", start: 1_990_000),
            track("ep#4", "Jon Hassell", "Dreamworld", start: 3_520_000),
            track("ep#5", "Björk", "Jóga", start: 3_900_000).replacingOccurrences(
                of: #""target": {"kind": "undecided"}"#,
                with: #""target": {"kind": "existing", "id": "r9", "label": "Jóga"}"#),
        ]
        let starts = [602_000, 1_095_000, 1_990_000, 3_520_000, 3_900_000]
        let entries = (1...5).map { n in
            #"{"position": \#(n), "track_key": "ep#\#(n)", "artist_text": "x", "title_text": "y", "start_ms": \#(starts[n - 1]), "end_ms": null, "offset_is_estimate": \#(n == 4)}"#
        }
        let issues = [
            issue(
                "uncertain_artist:Arca", kind: "uncertain_artist", subject: "Arca", type: "artist",
                candidates: [
                    candidate("a1", "Arca", score: 91, type: "artist"),
                    candidate("a2", "Arca", score: 64, type: "artist"),
                ],
                choices: ["pick_candidate", "create_new", "skip"]),
            issue(
                "uncertain_recording:ep#1", kind: "uncertain_recording", subject: "ep#1",
                candidates: [
                    candidate("r1", "Turiya & Ramakrishna", score: 82), candidate("r2", "Turiya (live)", score: 44),
                ],
                choices: ["pick_candidate", "create_new", "skip"]),
            issue(
                "metadata_not_found:ep#2", kind: "metadata_not_found", subject: "ep#2",
                choices: ["keep_as_entered", "skip"], message: "The station gave no title."),
            issue(
                "uncertain_recording:ep#4", kind: "uncertain_recording", subject: "ep#4",
                candidates: [candidate("r3", "Cocoon", score: 67)], choices: ["pick_candidate", "create_new", "skip"],
                answer: #"{"choice": "create_new", "candidate_id": null}"#),
        ]
        return
            #"{"media": [], "artists": [], "releases": [], "loose_tracks": [\#(tracks.joined(separator: ","))], "radio": {"show": {"station": "nts", "slug": "s", "name": "Late Night Tapes", "description": "", "image": null}, "episode": {"slug": "e", "name": "Turiya and friends", "description": "", "broadcast_at": null, "location": null, "url": "https://nts.live/x", "image": null, "genres": [], "moods": []}, "medium_key": "ep", "entries": [\#(entries.joined(separator: ","))]}, "issues": [\#(issues.joined(separator: ","))]}"#
    }()

    /// A folder job whose one issue has a kind the app has no words for.
    static let failureProposal =
        #"{"media": [], "artists": [], "releases": [], "loose_tracks": [], "radio": null, "issues": [\#(issue("unexpected_response:x", kind: "unexpected_response", subject: "https://nts.live/x", type: "radio_episode", choices: ["retry"], message: "NTS answered in a shape EP does not understand."))]}"#
}

private typealias F = ImportFixtures

/// Answers in turn from a list per path (the last answer repeats), by method and path
/// (GET: the path alone).
nonisolated final class SequenceTransport: @unchecked Sendable {
    private final class Answers: @unchecked Sendable {
        let lock = NSLock()
        var lists: [String: [(HTTPResponse.Status, String)]]
        init(_ lists: [String: [(HTTPResponse.Status, String)]]) { self.lists = lists }

        func next(_ key: String) -> (HTTPResponse.Status, String)? {
            lock.withLock {
                guard var list = lists[key], !list.isEmpty else { return nil }
                let first = list[0]
                if list.count > 1 { list.removeFirst() }
                lists[key] = list
                return first
            }
        }
    }

    let stub: StubTransport

    /// `GET /imports` is asked twice per load (`attention=true`, then `false`): both read
    /// the "/imports" answers in turn, each keeping the jobs of its half.
    init(_ answers: [String: [(HTTPResponse.Status, String)]]) {
        var lists = answers
        lists["/imports#attention"] = answers["/imports"]
        let answers = Answers(lists)
        stub = StubTransport { request in
            let path = request.path?.split(separator: "?").first.map(String.init) ?? ""
            let attention = request.path.flatMap { URLComponents(string: $0)?.queryItems }?
                .first { $0.name == "attention" }?.value.map { $0 == "true" }
            var key = request.method == .get ? path : "\(request.method.rawValue) \(path)"
            if key == "/imports", attention == true { key = "/imports#attention" }
            guard let (status, answer) = answers.next(key) else {
                return (
                    HTTPResponse(status: .notFound, headerFields: [.contentType: "application/json"]), Data("{}".utf8)
                )
            }
            var json = answer
            if path == "/imports", let attention,
                let jobs = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [[String: Any]]
            {
                let kept = jobs.filter { ["needs_review", "failed"].contains($0["state"] as? String) == attention }
                json = String(decoding: try JSONSerialization.data(withJSONObject: kept), as: UTF8.self)
            }
            var fields: HTTPFields = [.contentType: "application/json"]
            if let list = try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [Any] {
                fields[HTTPField.Name("X-Total-Count")!] = String(list.count)
            }
            return (HTTPResponse(status: status, headerFields: fields), Data(json.utf8))
        }
    }

    func count(_ path: String) -> Int {
        stub.requests.filter { $0.request.path?.split(separator: "?").first.map(String.init) == path }.count
    }
}

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct ImportStoreTests {
    private func store(_ transport: SequenceTransport) async -> ImportStore {
        let connection = await stubConnection(transport.stub)
        return ImportStore(connection: connection, interval: .milliseconds(10), toastDuration: .seconds(60))
    }

    @Test func pollsWhileAJobRunsAndStopsWhenIdle() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [
                (.ok, F.summary(running: 1, fraction: "0.25")), (.ok, F.summary(running: 1, fraction: "0.5")),
                (.ok, F.summary(review: 1)),
            ],
            "/imports": [(.ok, "[\(F.job("j1", state: "running"))]")],
        ])
        let store = await store(transport)
        store.refresh()
        #expect(store.isPolling)
        await store.settle()

        #expect(!store.isPolling)
        #expect(transport.count("/imports/summary") == 3)
        #expect(store.badge == .review(1))
        #expect(store.progress == nil)

        // Idle: a refresh asks once and stops.
        store.refresh()
        await store.settle()
        #expect(transport.count("/imports/summary") == 4)
    }

    @Test func pollingStopsWhenTheServerIsUnreachable() async throws {
        let up = Mutex(true)
        let summaries = Mutex(0)
        let transport = StubTransport { request in
            let path = request.path?.split(separator: "?").first.map(String.init) ?? ""
            if path == "/imports/summary" {
                summaries.withLock { $0 += 1 }
                guard up.withLock({ $0 }) else { throw URLError(.cannotConnectToHost) }
            }
            let json =
                switch path {
                case "/server": Fixtures.server
                case "/imports/summary": F.summary(running: 1, fraction: "0.5")
                default: "[]"
                }
            return (HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(json.utf8))
        }
        let connection = await stubConnection(transport)
        let store = ImportStore(connection: connection, interval: .milliseconds(10), toastDuration: .seconds(60))
        store.refresh()
        while summaries.withLock({ $0 }) < 3 { try await Task.sleep(for: .milliseconds(10)) }
        up.withLock { $0 = false }
        await store.settle()  // a running job keeps polling; unreachable ends it
        #expect(!connection.isConnected)
        #expect(!store.isPolling)
        let asked = summaries.withLock { $0 }
        try await Task.sleep(for: .milliseconds(100))
        #expect(summaries.withLock { $0 } == asked)
    }

    @Test func theBadgeAndRingFollowTheSummary() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [(.ok, F.summary(running: 1, review: 3, failed: 1, fraction: "0.62"))],
            "/imports": [(.ok, "[]")],
        ])
        let store = await store(transport)
        store.refresh()
        while store.summary == nil { try await Task.sleep(for: .milliseconds(10)) }
        store.stop()
        #expect(store.badge == .failed)
        #expect(store.progress == 0.62)
        #expect(store.accessibilitySummary == "Import — 1 running, 3 waiting for review, 1 failed")
    }

    @Test func aJobThatFinishesOrNeedsReviewShowsAToastAndReloadsTheLibrary() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [
                (.ok, F.summary(running: 2)), (.ok, F.summary(running: 1)), (.ok, F.summary(review: 1)),
            ],
            "/imports": [
                (.ok, "[\(F.job("j1", state: "running")), \(F.job("j2", state: "queued"))]"),
                (.ok, "[\(F.job("j1", state: "done", created: F.created)), \(F.job("j2", state: "running"))]"),
                (
                    .ok,
                    "[\(F.job("j1", state: "done", created: F.created)), \(F.job("j2", state: "needs_review", actions: ["review"]))]"
                ),
            ],
        ])
        let store = await store(transport)
        store.refresh()
        await store.settle()

        let toast = try #require(store.toast)
        #expect(toast.kind == .review && toast.jobID == "j2")
        #expect(toast.title == "Cocteau Twins — Treasure is ready for review")
        #expect(store.libraryChanges == 1)  // j1 created rows; j2 created nothing
        #expect(store.firstToReview?.id == "j2")
        store.dismissToast()
        #expect(store.toast == nil)
    }

    @Test func theFirstLoadIsNoChange() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [(.ok, F.summary(review: 1))],
            "/imports": [(.ok, "[\(F.job("j1", state: "needs_review"))]")],
        ])
        let store = await store(transport)
        store.refresh()
        await store.settle()
        #expect(store.jobs.count == 1)
        #expect(store.toast == nil && store.libraryChanges == 0)
    }

    /// Jobs needing attention (`attention=true`) are listed apart from, and above, the others.
    @Test func jobsNeedingAttentionAreLoadedApart() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [(.ok, F.summary(review: 1))],
            "/imports": [
                (
                    .ok,
                    "[\(F.job("j1", state: "queued")), \(F.job("j2", state: "needs_review", actions: ["review"])), \(F.job("j3", state: "failed"))]"
                )
            ],
        ])
        let store = await store(transport)
        store.refresh()
        await store.settle()
        #expect(store.attentionJobs.map(\.id) == ["j2", "j3"])
        #expect(store.otherJobs.map(\.id) == ["j1"])
        #expect(store.jobs.map(\.id) == ["j2", "j3", "j1"])
        #expect(store.firstToReview?.id == "j2")
        let queries = transport.stub.requests.compactMap { $0.request.path }.filter { $0.hasPrefix("/imports?") }
        #expect(queries.contains { $0.contains("attention=true") })
        #expect(queries.contains { $0.contains("attention=false") && $0.contains("state=") })
    }

    @Test func retryDismissAndCancelCallTheirEndpoints() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [(.ok, F.summary(failed: 1))],
            "/imports": [
                (
                    .ok,
                    "[\(F.job("j1", state: "failed", actions: ["retry", "dismiss"])), \(F.job("j2", state: "queued", actions: ["cancel"]))]"
                )
            ],
            "POST /imports/j1/retry": [(.ok, F.read("j1", state: "queued", actions: ["cancel"]))],
            "POST /imports/j1/dismiss": [(.conflict, #"{"detail": "a queued job cannot be dismissed"}"#)],
            "POST /imports/j2/cancel": [(.ok, F.read("j2", state: "dismissed"))],
        ])
        let store = await store(transport)
        store.refresh()
        await store.settle()

        await store.perform(.retry, on: "j1")
        #expect(store.job(id: "j1")?.state.known == .queued)
        await store.perform(.dismiss, on: "j1")
        #expect(store.actionError?.contains("a queued job cannot be dismissed") == true)
        store.stop()
        await store.perform(.cancel, on: "j2")
        #expect(store.job(id: "j2") == nil)
        #expect(transport.count("/imports/j2/cancel") == 1)
        store.stop()
    }
}

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct ImportDraftTests {
    @Test func aPastedLinkIsClassifiedByTheServerAndStarted() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/links/classify": [
                (
                    .ok,
                    #"{"link_kind": "bandcamp_album", "source_kind": "bandcamp", "import_kind": "bandcamp", "url": "https://x.bandcamp.com/album/y"}"#
                ),
                (
                    .ok,
                    #"{"link_kind": "spotify", "source_kind": "spotify", "import_kind": null, "url": "https://open.spotify.com/album/1"}"#
                ),
            ],
            "POST /imports": [(.created, F.read("j9", state: "queued"))],
        ])
        let draft = ImportDraft(connection: await stubConnection(transport.stub), debounce: .zero)
        draft.update("https://x.bandcamp.com/album/y")
        #expect(draft.detected == .checking)
        await draft.settle()
        #expect(draft.pill == "Bandcamp album" && draft.canStart)

        let job = try #require(await draft.start())
        #expect(job.id == "j9")
        #expect(draft.text.isEmpty)
        let body = try #require(transport.stub.requests.last?.body)
        let sent = try #require(try JSONSerialization.jsonObject(with: body) as? [String: String])
        #expect(sent == ["kind": "bandcamp", "input": "https://x.bandcamp.com/album/y"])

        draft.update("https://open.spotify.com/album/1")
        await draft.settle()
        #expect(draft.detected == .notImportable(name: "Spotify") && !draft.canStart)
        draft.update("hello")
        #expect(draft.detected == .notImportable(name: nil))
        draft.update("~/Music/Incoming")
        #expect(draft.detected == .folder && draft.canStart)
    }

    @Test func aFolderOutsideTheMediaRootsNamesTheFoldersTheServerReads() async throws {
        let server = Fixtures.server.replacingOccurrences(
            of: #""media_roots": []"#,
            with:
                #""media_roots": [{"path": "/library", "host_path": "/Users/x/Music/ep-Library"}, {"path": "/watch/Library", "host_path": "/Users/x/Music/Library"}]"#
        )
        let refused =
            #"{"detail": [{"loc": ["body", "input"], "msg": "must be under one of the media roots", "type": "value_error", "input": "/tmp/x"}]}"#
        let transport = SequenceTransport([
            "/server": [(.ok, server)], "POST /imports": [(.unprocessableContent, refused)],
        ])
        let draft = ImportDraft(connection: await stubConnection(transport.stub), debounce: .zero)
        #expect(await draft.startFolder(path: "/tmp/x") == nil)
        let error = try #require(draft.error)
        #expect(error.contains("can’t read this folder") && error.contains("/Users/x/Music/Library"))
    }
}

@MainActor
@Suite(.timeLimit(.minutes(1)))
struct ReviewModelTests {
    private func model(_ proposal: String = F.radioProposal) async throws -> ReviewModel {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/j1": [(.ok, F.read("j1", state: "needs_review", proposal: proposal, actions: ["review"]))],
        ])
        let model = ReviewModel(jobID: "j1", connection: await stubConnection(transport.stub))
        await model.load()
        return model
    }

    @Test func questionsFollowTheTracklistWithDefaultsPreselected() async throws {
        let model = try await model()
        try #require(model.phase == .ready)
        // Open questions only, in tracklist order; the artist question sits before its entry.
        #expect(
            model.visibleRows.map(\.id) == [
                "uncertain_recording:ep#1", "metadata_not_found:ep#2", "uncertain_artist:Arca",
            ])
        #expect(model.selectedID == "uncertain_recording:ep#1")
        let first = try #require(model.selected)
        #expect(first.time == "10:02" && first.title == "Alice Coltrane — Turiya")
        #expect(first.question == "Same recording as library track?")
        #expect(model.pick(for: first)?.candidateID == "r1")
        #expect(first.options.map(\.key) == ["1", "2", "N", "U"])
        #expect(first.options.last?.title == "Leave unmatched (plain text)")
        let second = try #require(model.visibleRows.dropFirst().first)
        #expect(model.pick(for: second)?.choice == .keepAsEntered)
        // All: the answered question (its answer kept) and the matched entry too.
        model.filter = .all
        #expect(model.visibleRows.count == 6)
        let answered = try #require(model.rows.first { $0.id == "uncertain_recording:ep#4" })
        #expect(model.pick(for: answered)?.choice == .createNew && answered.timeIsEstimate)
        let matched = try #require(model.rows.last)
        #expect(matched.issue == nil && matched.outcome == "In library — Jóga")
        #expect(model.headline.items == "5 entries · 1 matched confidently")
        #expect(model.headline.decisions == "3 need a decision")
    }

    @Test func theKeyboardChoosesAndMovesOn() async throws {
        let model = try await model()
        #expect(model.press("2"))
        #expect(model.pick(for: try #require(model.selected))?.candidateID == "r2")
        #expect(model.press("n"))
        #expect(model.pick(for: try #require(model.selected))?.choice == .createNew)
        #expect(!model.press("k"))  // not one of this question's options
        model.acceptAndNext()
        #expect(model.selectedID == "metadata_not_found:ep#2")
        #expect(model.accepted == ["uncertain_recording:ep#1"])
        model.move(1)
        #expect(model.selectedID == "uncertain_artist:Arca")
        model.move(1)
        #expect(model.selectedID == "uncertain_artist:Arca")
        model.move(-2)
        #expect(model.selectedID == "uncertain_recording:ep#1")
        // A library row found by search becomes a candidate and the pick.
        model.addCandidate(id: "r7", title: "Turiya (studio)", detail: "Björk")
        let row = try #require(model.selected)
        #expect(model.pick(for: row)?.candidateID == "r7" && row.options[2].key == "3")
    }

    @Test func confirmSendsTheOpenQuestionsAndChangedAnswers() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/j1": [(.ok, F.read("j1", state: "needs_review", proposal: F.radioProposal))],
            "POST /imports/j1/review": [(.ok, F.read("j1", state: "queued"))],
        ])
        let model = ReviewModel(jobID: "j1", connection: await stubConnection(transport.stub))
        await model.load()
        model.press("u")
        let job = try #require(await model.confirm())
        #expect(job.state == .queued)

        let body = try #require(transport.stub.requests.last?.body)
        let sent = try #require(try JSONSerialization.jsonObject(with: body) as? [String: [[String: Any]]])
        let answers = try #require(sent["answers"])
        #expect(answers.count == 3)  // the answered entry 4 is left as it is
        let byIssue = Dictionary(uniqueKeysWithValues: answers.map { ($0["issue_id"] as? String ?? "", $0) })
        #expect(byIssue["uncertain_recording:ep#1"]?["choice"] as? String == "skip")
        #expect(byIssue["metadata_not_found:ep#2"]?["choice"] as? String == "keep_as_entered")
        #expect(byIssue["uncertain_artist:Arca"]?["candidate_id"] as? String == "a1")
    }

    @Test func aKindWithoutItsOwnWordsShowsItsMessageAndChoices() async throws {
        let model = try await model(F.failureProposal)
        let row = try #require(model.selected)
        #expect(row.question == "NTS answered in a shape EP does not understand.")
        let issue = try #require(row.issue)
        #expect(ReviewModel.question(for: issue) == nil)
        #expect(row.options.map(\.title) == ["Try again"] && model.pick(for: row)?.choice == .retry)
        #expect(model.answers.map(\.choice.known) == [.retry])
    }

    /// Kinds, states and choices a newer server adds decode (open enums) and reach the
    /// generic path: the message is the question, unknown choices are not offered.
    @Test func aKindNewerThanTheAppDecodesAndRendersGenerically() async throws {
        let proposal =
            #"{"media": [], "artists": [], "releases": [], "loose_tracks": [], "radio": null, "issues": [\#(F.issue("future_kind:x", kind: "future_kind", subject: "x", type: "medium", choices: ["skip", "future_choice"], message: "Something new happened."))]}"#
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/j1": [(.ok, F.read("j1", state: "archived", proposal: proposal, actions: ["review"]))],
        ])
        let model = ReviewModel(jobID: "j1", connection: await stubConnection(transport.stub))
        await model.load()
        try #require(model.phase == .ready)
        #expect(model.job?.state.known == nil && model.job?.state.rawValue == "archived")
        let row = try #require(model.selected)
        let issue = try #require(row.issue)
        #expect(issue.kind.known == nil && issue.kind.rawValue == "future_kind")
        #expect(row.question == "Something new happened." && ReviewModel.question(for: issue) == nil)
        #expect(row.options.map(\.choice) == [.skip])
        #expect(model.answers.map(\.choice.known) == [.skip])
    }

    /// A job queued again after review reads with its answers (`Issue.answer`); the
    /// queue keeps them for the expanded row.
    @Test func aQueuedJobShowsTheAnswersGiven() async throws {
        let transport = SequenceTransport([
            "/server": [(.ok, Fixtures.server)],
            "/imports/summary": [(.ok, F.summary())],
            "/imports": [(.ok, "[\(F.job("j1", state: "queued"))]")],
        ])
        let store = ImportStore(connection: await stubConnection(transport.stub))
        let read = try JSONDecoder.iso8601Fractional.decode(
            Components.Schemas.ImportJobRead.self,
            from: Data(F.read("j1", state: "queued", proposal: F.radioProposal).utf8))
        store.started(read)
        await store.settle()
        let answered = try #require(store.answered["j1"])
        #expect(answered.map(\.id) == ["uncertain_recording:ep#4"])
        #expect(ReviewModel.answerTitle(for: answered[0]) == "New recording")
    }
}
