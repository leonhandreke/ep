import EPAPI
import Foundation
import Observation

/// A recording's sources (board 5, decision 6): every file, range and link it can be
/// played or fetched from, which one is preferred, and the changes the popover makes:
/// choosing the preferred source (or back to the default), locating a missing file, and
/// adding a file or a link. Afterwards the sources are read again and `onChange` lets the
/// window reload what shows play states.
@Observable
final class SourcesStore {
    typealias Phase = LibraryBrowser.Phase

    let recordingID: String
    private(set) var sources: [Components.Schemas.RecordingSourceRead] = []
    private(set) var phase = Phase.idle
    /// The last change failed (shown in the popover until the next one).
    private(set) var error: String?
    /// The source that plays when no preference is set (from the track's playability).
    private(set) var playingSourceID: String?

    @ObservationIgnored var onChange: (() -> Void)?
    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private var task: Task<Void, Never>?

    init(recordingID: String, playingSourceID: String?, connection: ServerConnection) {
        self.recordingID = recordingID
        self.playingSourceID = playingSourceID
        self.connection = connection
    }

    /// The option shown as chosen: the preferred source, else the one that plays.
    var chosenID: String? {
        sources.first(where: \.preferred)?.source.id ?? playingSourceID
    }

    var hasPreference: Bool { sources.contains(where: \.preferred) }

    func load() {
        if sources.isEmpty { phase = .loading }
        run(reload: false) {}
    }

    /// Makes `sourceID` the one that plays; nil goes back to the default.
    func choose(_ sourceID: String?) {
        let client = connection.client
        let id = recordingID
        run {
            if let sourceID {
                _ = try await client.updateRecording(path: .init(id: id), body: .json(.init(preferredSourceId: sourceID)))
                    .ok
            } else {
                typealias Keys = Components.Schemas.RecordingUpdate.CodingKeys
                _ = try await withExplicitNulls(Keys.preferredSourceId) {
                    try await client.updateRecording(path: .init(id: id), body: .json(.init())).ok
                }
            }
        }
    }

    /// Locate…: the file of `fileSourceID` is now at `path` (a path on this Mac; the
    /// server maps it to its own).
    func locate(_ fileSourceID: String, at path: String) {
        let client = connection.client
        run {
            _ = try await client.updateSource(path: .init(id: fileSourceID), body: .json(.init(uri: path))).ok
        }
    }

    /// Adds a file on this Mac as a source of the recording.
    func addFile(at path: String) {
        let client = connection.client
        let id = recordingID
        run {
            _ = try await client.createSource(body: .json(.init(kind: .file, recordingId: id, uri: path))).created
        }
    }

    /// Adds a link (Bandcamp, YouTube, …) as a source; the server says what kind it is.
    func addLink(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let client = connection.client
        let id = recordingID
        run {
            let info = try await client.classifyLink(query: .init(url: text)).ok.body.json
            guard let kind = info.sourceKind, kind != .file, kind != .slice else {
                throw SourcesError.unknownLink
            }
            _ = try await client.createSource(body: .json(.init(kind: kind, recordingId: id, uri: info.url ?? text)))
                .created
        }
    }

    /// Waits for the change in flight (tests).
    func settle() async { await task?.value }

    private func run(reload: Bool = true, _ change: @escaping () async throws -> Void) {
        error = nil
        let previous = task
        let client = connection.client
        let id = recordingID
        task = Task {
            await previous?.value
            do {
                try await change()
                let sources = try await client.listRecordingSources(path: .init(id: id)).ok.body.json
                self.sources = sources
                phase = .loaded
                if reload {
                    // What plays may have changed: the track's playability says which.
                    let recording = try? await client.getRecording(path: .init(id: id)).ok.body.json
                    playingSourceID = recording?.playability.playback?.sourceId
                    onChange?()
                }
            } catch {
                guard !ServerConnection.isCancellation(error) else { return }
                if case SourcesError.unknownLink = error {
                    self.error = String(localized: "EP doesn’t know this kind of link.")
                } else {
                    self.error = String(localized: "Couldn’t change the sources.")
                }
                if sources.isEmpty { phase = .failed(String(localized: "Couldn’t load the sources.")) }
                connection.reportFailure(error)
            }
        }
    }

    private enum SourcesError: Error { case unknownLink }
}

// MARK: - What a source option shows

extension Components.Schemas.RecordingSourceRead {
    /// "File", "Range in a file", "Range in radio episode", "Link".
    var title: String {
        switch role {
        case .file: String(localized: "File")
        case .slice: String(localized: "Range in a file")
        case .radioExcerpt: String(localized: "Range in radio episode")
        case .link: String(localized: "Link")
        }
    }

    /// The codec chip: "FLAC 16/44.1", "MP3 320", "OPUS".
    var codecChip: String? {
        guard let file, let codec = file.codec ?? source.codec else { return nil }
        let name = codec.uppercased()
        if let depth = file.bitDepth, let rate = file.sampleRate {
            // A technical figure, written the same everywhere: "44.1".
            let kHz = (Double(rate) / 1000).formatted(
                .number.precision(.fractionLength(0...1)).locale(Locale(identifier: "en_US_POSIX")))
            return "\(name) \(depth)/\(kHz)"
        }
        if let bitRate = file.bitRate { return "\(name) \(bitRate / 1000)" }
        return name
    }

    /// Where it came from: "from Bandcamp · 3 Jan 2026"; a link: "YouTube".
    var provenance: String {
        if role == .link { return source.kind.displayName }
        let origin = ancestors.first { $0.kind != .file && $0.kind != .slice }.map {
            String(localized: "from \($0.kind.displayName)")
        }
        let added = source.createdAt.formatted(.dateTime.day().month(.abbreviated).year())
        return [origin, added].compactMap(\.self).joined(separator: " · ")
    }

    /// The second line: the file's path on this Mac (else the server's), the episode and
    /// time range of an excerpt, or the link.
    var location: String {
        switch role {
        case .radioExcerpt:
            let episode = radio.map { radio in
                [radio.show.name, RadioFormat.day(radio.episode.broadcastAt)].compactMap(\.self)
                    .joined(separator: " — ")
            }
            return [episode, range].compactMap(\.self).joined(separator: " · ")
        case .link:
            return source.uri ?? ""
        case .file, .slice:
            let path = file.map { $0.hostPath ?? $0.path } ?? source.uri ?? ""
            return [path, role == .slice ? range : nil].compactMap(\.self).joined(separator: " · ")
        }
    }

    /// "28:02 – 32:51".
    private var range: String? {
        guard let start = LibraryFormat.duration(ms: source.startMs) else { return nil }
        guard let end = LibraryFormat.duration(ms: source.endMs) else { return start }
        return "\(start) – \(end)"
    }

    /// A known file that is not at its path.
    var isMissing: Bool { file.map { !$0.exists } ?? false }
}
