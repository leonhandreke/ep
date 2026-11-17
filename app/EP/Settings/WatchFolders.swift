import EPAPI
import Foundation
import Observation

/// The server's watched folders (Settings §10, the empty library's "Watch a folder…"):
/// `GET/POST/PATCH/DELETE /watch-dirs`. Folders are sent as paths on this Mac; the server
/// maps them (`EP_HOST_PATHS`) and refuses one outside its media roots. One store for the
/// app, loaded when Settings or the empty library shows it.
@Observable
final class WatchFolders {
    typealias Folder = Components.Schemas.WatchDirRead

    private(set) var folders: [Folder] = []
    private(set) var isLoaded = false
    /// The last request's failure, in words.
    private(set) var error: String?

    @ObservationIgnored private let connection: ServerConnection

    init(connection: ServerConnection) {
        self.connection = connection
    }

    func load() async {
        do {
            let folders = try await connection.client.listWatchDirs().ok.body.json
            self.folders = folders
            isLoaded = true
            error = nil
        } catch {
            fail(error, String(localized: "Couldn’t load the watched folders."))
        }
    }

    /// Watches `path` (a folder on this Mac). What is in it already is not imported; the
    /// caller may ask the server to with `importExisting(_:)`.
    func add(path: String) async -> Folder? {
        do {
            switch try await connection.client.createWatchDir(body: .json(.init(path: path))) {
            case .created(let created):
                let folder = try created.body.json
                folders = (folders.filter { $0.id != folder.id } + [folder]).sorted { $0.displayPath < $1.displayPath }
                error = nil
                return folder
            case .conflict:
                error = String(localized: "This folder is watched already.")
            case .unprocessableContent(let refused):
                let messages = (try? refused.body.json.detail?.map(\.msg)) ?? []
                error = ImportDraft.explain(messages, kind: .folder, roots: mediaRoots)
            case .undocumented(let status, _):
                error = String(localized: "The server answered \(status).")
            }
        } catch {
            fail(error, String(localized: "Couldn’t watch this folder."))
        }
        return nil
    }

    func setEnabled(_ enabled: Bool, for id: String) async {
        do {
            let folder = try await connection.client.updateWatchDir(
                path: .init(id: id), body: .json(.init(enabled: enabled))
            ).ok.body.json
            replace(folder)
            error = nil
        } catch {
            fail(error, String(localized: "Couldn’t change the folder."))
        }
    }

    func remove(_ id: String) async {
        do {
            _ = try await connection.client.deleteWatchDir(path: .init(id: id)).noContent
            folders.removeAll { $0.id == id }
            error = nil
        } catch {
            fail(error, String(localized: "Couldn’t stop watching the folder."))
        }
    }

    /// Imports the album folders already in a watched folder (`POST /watch-dirs/{id}/scan`);
    /// the number of imports queued.
    @discardableResult
    func importExisting(_ id: String) async -> Int? {
        do {
            let jobs = try await connection.client.scanWatchDir(path: .init(id: id), body: .json(.init())).ok.body.json
            error = nil
            return jobs.count
        } catch {
            fail(error, String(localized: "Couldn’t import what is in the folder."))
            return nil
        }
    }

    private func replace(_ folder: Folder) {
        if let index = folders.firstIndex(where: { $0.id == folder.id }) { folders[index] = folder }
    }

    private func fail(_ error: any Error, _ message: String) {
        guard !ServerConnection.isCancellation(error) else { return }
        connection.reportFailure(error)
        self.error = message
    }

    private var mediaRoots: [String] {
        guard case .connected(let server) = connection.state else { return [] }
        return server.mediaRoots.map { ImportFormat.abbreviatingHome($0.hostPath ?? $0.path) }
    }
}

extension Components.Schemas.WatchDirRead {
    /// The folder as this Mac sees it ("~/Downloads/Music"), else the server's path.
    var displayPath: String { ImportFormat.abbreviatingHome(hostPath ?? path) }

    /// "last import 21:02", "importing", "idle", "paused" (§10).
    func statusText(now: Date = .now) -> String {
        switch status {
        case .paused: return String(localized: "paused")
        case .importing: return String(localized: "importing")
        case .idle:
            guard let last = lastImportAt else { return String(localized: "idle") }
            let when =
                Calendar.autoupdatingCurrent.isDate(last, inSameDayAs: now)
                ? last.formatted(date: .omitted, time: .shortened) : ImportFormat.time(last, now: now)
            return String(localized: "last import \(when)")
        }
    }
}

extension Components.Schemas.LibraryCountsRead {
    /// Nothing in the library and no radio either: the first launch (§11.1).
    var isEmptyLibrary: Bool {
        artists + radioOnlyArtists + releaseGroups + recordings + radioShows + radioEpisodes == 0
    }
}
