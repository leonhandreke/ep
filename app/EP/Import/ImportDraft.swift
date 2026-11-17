import EPAPI
import Foundation
import Observation

/// The import sheet's start section (design 7.1): the link field and what the server says
/// the link is (`GET /links/classify`: the kind pill), or a folder; starting the job.
@Observable
final class ImportDraft {
    /// What the field holds, as far as the server (or the path) tells.
    enum Detected: Equatable {
        case none
        case checking
        /// A link EP imports: the pill ("Bandcamp album") and the import kind.
        case link(name: String, kind: Components.Schemas.ImportKind)
        /// A link EP knows but does not import (SoundCloud, Spotify), or does not know.
        case notImportable(name: String?)
        /// An absolute path typed or pasted: a folder import.
        case folder
    }

    private(set) var text = ""
    private(set) var detected = Detected.none
    private(set) var isStarting = false
    /// Why the last start failed (the server's 422, e.g. a folder outside its media roots).
    private(set) var error: String?

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private let debounce: Duration
    @ObservationIgnored private var classifyTask: Task<Void, Never>?
    @ObservationIgnored private var generation = 0

    init(connection: ServerConnection, text: String = "", debounce: Duration = .milliseconds(200)) {
        self.connection = connection
        self.debounce = debounce
        update(text)
    }

    var canStart: Bool {
        switch detected {
        case .link, .folder: !isStarting
        default: false
        }
    }

    /// The pill's text.
    var pill: String? {
        switch detected {
        case .none, .checking: nil
        case .link(let name, _): name
        case .notImportable(let name):
            name.map { String(localized: "\($0) · not importable") } ?? String(localized: "Not a link EP imports")
        case .folder: String(localized: "Folder")
        }
    }

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }

    func update(_ text: String) {
        guard text != self.text else { return }
        self.text = text
        error = nil
        classify()
    }

    /// Waits for the classification in flight (tests).
    func settle() async { await classifyTask?.value }

    private func classify() {
        classifyTask?.cancel()
        generation += 1
        let current = generation
        let value = trimmed
        if value.isEmpty {
            detected = .none
            return
        }
        if value.hasPrefix("/") || value.hasPrefix("~/") {
            detected = .folder
            return
        }
        guard let url = URL(string: value), let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
            url.host != nil
        else {
            detected = .notImportable(name: nil)
            return
        }
        detected = .checking
        let client = connection.client
        let debounce = debounce
        classifyTask = Task {
            do {
                if debounce > .zero { try await Task.sleep(for: debounce) }
                let info = try await client.classifyLink(query: .init(url: value)).ok.body.json
                guard !Task.isCancelled, current == generation else { return }
                if let kind = info.importKind {
                    detected = .link(name: info.linkKind?.displayName ?? kind.displayName, kind: kind)
                } else {
                    detected = .notImportable(name: info.linkKind?.displayName)
                }
            } catch {
                guard !Task.isCancelled, current == generation, !ServerConnection.isCancellation(error) else { return }
                detected = .notImportable(name: nil)
                connection.reportFailure(error)
            }
        }
    }

    /// Starts importing what the field holds; nil when the server refused it (`error`).
    func start() async -> Components.Schemas.ImportJobRead? {
        switch detected {
        case .link(_, let kind):
            return await start(kind: kind, input: trimmed)
        case .folder:
            return await startFolder(path: (trimmed as NSString).expandingTildeInPath)
        default:
            return nil
        }
    }

    /// Imports a folder (chosen, dropped or typed), given as the path on this Mac: the
    /// server maps it to its own (`EP_HOST_PATHS`) and refuses one outside its media roots.
    func startFolder(path: String) async -> Components.Schemas.ImportJobRead? {
        await start(kind: .folder, input: path)
    }

    private func start(kind: Components.Schemas.ImportKind, input: String) async -> Components.Schemas.ImportJobRead? {
        isStarting = true
        error = nil
        defer { isStarting = false }
        do {
            switch try await connection.client.createImportJob(body: .json(.init(input: input, kind: kind))) {
            case .created(let created):
                let job = try created.body.json
                text = ""
                detected = .none
                return job
            case .unprocessableContent(let refused):
                let messages = (try? refused.body.json.detail?.map(\.msg)) ?? nil
                error = Self.explain(messages ?? [], kind: kind, roots: mediaRoots)
            case .undocumented(let status, _):
                error = String(localized: "The server answered \(status).")
            }
        } catch {
            guard !ServerConnection.isCancellation(error) else { return nil }
            connection.reportFailure(error)
            self.error = String(localized: "The server can’t be reached.")
        }
        return nil
    }

    /// The directories the server reads, as paths on this Mac where it says so.
    var mediaRoots: [String] {
        guard case .connected(let server) = connection.state else { return [] }
        return server.mediaRoots.map { ImportFormat.abbreviatingHome($0.hostPath ?? $0.path) }
    }

    /// The server's 422 in words: a folder outside the media roots names the folders it
    /// can read.
    static func explain(_ messages: [String], kind: Components.Schemas.ImportKind, roots: [String]) -> String {
        if kind == .folder, messages.contains(where: { $0.contains("media roots") }) {
            return roots.isEmpty
                ? String(localized: "The server can’t read this folder.")
                : String(
                    localized: "The server can’t read this folder. It reads only \(roots.joined(separator: ", ")).")
        }
        if kind == .folder, messages.contains(where: { $0.contains("not a directory") }) {
            return String(localized: "That is not a folder the server can see.")
        }
        guard let first = messages.first else { return String(localized: "The server refused this import.") }
        return String(localized: "The server refused this import: \(first).")
    }
}
