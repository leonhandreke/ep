import Foundation
import Observation

/// Space in the review sheet (design 7.2, "space preview audio"): plays the selected
/// incoming track from the job's files (`GET /imports/{id}/stream?track_key=`), its
/// range of the medium's file, through an engine of its own. The app's player pauses
/// meanwhile; the preview stops at the end of the track, on Space, or when the sheet
/// closes or another question is chosen.
@Observable
final class ReviewPreview {
    /// The row whose track plays (or is being opened).
    private(set) var rowID: String?
    /// Why the last preview did not play ("not downloaded yet").
    private(set) var message: String?

    @ObservationIgnored private let makeEngine: () -> any PlaybackEngine
    @ObservationIgnored private var engine: (any PlaybackEngine)?
    @ObservationIgnored private var task: Task<Void, Never>?

    init(makeEngine: @escaping () -> any PlaybackEngine = { AudioOutput() }) {
        self.makeEngine = makeEngine
    }

    /// The stream of a track of job `jobID` on `server`.
    static func url(server: URL, jobID: String, trackKey: String) -> URL {
        server.appending(path: "imports/\(jobID)/stream").appending(queryItems: [
            URLQueryItem(name: "track_key", value: trackKey)
        ])
    }

    /// Plays `preview` for row `rowID`, or stops when that row plays already.
    func toggle(_ preview: ReviewModel.Preview, rowID: String, url: URL) {
        if self.rowID == rowID { return stop() }
        stop()
        self.rowID = rowID
        message = nil
        let engine = engine ?? makeEngine()
        self.engine = engine
        engine.onEnd = { [weak self] in self?.stop() }
        engine.onFailure = { [weak self] _ in self?.fail() }
        task = Task {
            do {
                try await engine.load(url, mediaType: preview.mediaType, at: Double(preview.startMS) / 1000)
                guard !Task.isCancelled, self.rowID == rowID else { return }
                engine.play()
                // Stop at the end of the track's range (the medium may hold more: an episode).
                guard let end = preview.endMS else { return }
                while !Task.isCancelled, self.rowID == rowID {
                    if engine.currentTime * 1000 >= Double(end) { return stop() }
                    try await Task.sleep(for: .milliseconds(250))
                }
            } catch {
                guard !Task.isCancelled, self.rowID == rowID, !(error is CancellationError) else { return }
                fail()
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        engine?.stop()
        rowID = nil
    }

    private func fail() {
        stop()
        message = String(localized: "This track’s audio isn’t there yet.")
    }
}
