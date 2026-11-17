import EPAPI
import Foundation
import Observation
import OpenAPIRuntime
import SwiftUI
import UniformTypeIdentifiers

/// What an edit sheet edits (context menu "Edit…", ⌘E on the selection; decision 6).
nonisolated enum EditTarget: Hashable, Sendable {
    case artist(String)
    case releaseGroup(String)
    /// A track edits its recording (title, credit, length): what every album and radio
    /// excerpt of it shows.
    case recording(String)
}

/// Loading and saving shared by the edit sheets' stores.
enum EditorPhase: Equatable {
    case loading, ready
    case failed(String)
}

/// A new cover picked or dropped in the album sheet, uploaded on Save.
struct NewCover: Equatable {
    var data: Data
    var type: UTType
}

/// The album sheet (board 9): the release group's fields, its tracks (order, credits,
/// "+ Track") and its cover. Save sends only what changed: the PATCH, the cover, each
/// changed credit, the new tracks, then the order.
@Observable
final class AlbumEditor {
    let id: String
    private(set) var phase = EditorPhase.loading
    var form: AlbumForm?
    var tracks: [TrackEditRow] = []
    private(set) var coverURL: String?
    var newCover: NewCover?
    private(set) var isSaving = false
    private(set) var error: String?

    @ObservationIgnored private var original: AlbumForm?
    @ObservationIgnored private var originalOrder: [String] = []
    @ObservationIgnored private let connection: ServerConnection

    init(id: String, connection: ServerConnection) {
        self.id = id
        self.connection = connection
    }

    var hasChanges: Bool {
        guard let form, let original else { return false }
        return form.patch(from: original) != nil || newCover != nil || tracks.contains(where: \.creditChanged)
            || TrackOrder.changed(tracks, from: originalOrder)
    }

    func load() async {
        let client = connection.client
        do {
            async let detail = client.getReleaseGroup(path: .init(id: id)).ok.body.json
            let list = try await allPages { limit, offset in
                let ok = try await client.listReleaseGroupTracks(
                    path: .init(id: id), query: .init(limit: limit, offset: offset)
                ).ok
                return (try ok.body.json, ok.headers.xTotalCount)
            }
            let group = try await detail
            let form = AlbumForm(group)
            self.form = form
            original = form
            coverURL = group.coverUrl
            tracks = list.map(TrackEditRow.init)
            originalOrder = list.map(\.id)
            phase = .ready
        } catch {
            phase = .failed(String(localized: "Couldn’t load the album."))
            connection.reportFailure(error)
        }
    }

    func moveTracks(from source: IndexSet, to destination: Int) {
        tracks.move(fromOffsets: source, toOffset: destination)
    }

    func addTrack(_ title: String) {
        let title = title.trimmingCharacters(in: .whitespaces)
        guard !title.isEmpty else { return }
        tracks.append(TrackEditRow(newTitle: title, disc: tracks.last?.disc ?? 1))
    }

    /// Saves; true when everything went to the server.
    func save() async -> Bool {
        guard let form, let original, form.isValid else { return false }
        isSaving = true
        error = nil
        defer { isSaving = false }
        let client = connection.client
        let id = id
        do {
            if let patch = form.patch(from: original) {
                _ = try await withExplicitNulls(patch.nulls) {
                    try await client.updateReleaseGroup(path: .init(id: id), body: .json(patch.body)).ok
                }
                self.original = form
            }
            if let cover = newCover {
                let body: Operations.SetReleaseGroupCover.Input.Body =
                    cover.type.conforms(to: .png)
                    ? .png(.init(cover.data))
                    : cover.type.conforms(to: .webP) ? .imageWebp(.init(cover.data)) : .jpeg(.init(cover.data))
                coverURL = try await client.setReleaseGroupCover(path: .init(id: id), body: body).ok.body.json.coverUrl
                newCover = nil
            }
            for index in tracks.indices where tracks[index].creditChanged {
                guard let recordingID = tracks[index].recordingID else { continue }
                _ = try await client.updateRecording(
                    path: .init(id: recordingID), body: .json(.init(artistCredit: Credit.write(tracks[index].credit)))
                ).ok
                tracks[index].originalCredit = tracks[index].credit
            }
            if TrackOrder.changed(tracks, from: originalOrder) {
                // A track made here is kept as a track of the album at once, so a Save that
                // fails later and is repeated does not make it twice.
                for index in tracks.indices where tracks[index].trackID == nil {
                    let row = tracks[index]
                    let track = try await client.createTrack(
                        body: .json(.init(disc: row.disc, newRecording: .init(title: row.title), releaseGroupId: id))
                    ).created.body.json
                    tracks[index].trackID = track.id
                    tracks[index].recordingID = track.recording.id
                }
                let places = TrackOrder.places(tracks)
                let ordered = try await client.setReleaseGroupTrackOrder(
                    path: .init(id: id), body: .json(.init(tracks: places))
                ).ok.body.json
                tracks = ordered.map(TrackEditRow.init)
                originalOrder = ordered.map(\.id)
            }
            return true
        } catch {
            self.error = EditErrors.describe(error)
            connection.reportFailure(error)
            return false
        }
    }
}

/// The artist sheet: name, sort name, disambiguation, type, MusicBrainz ID.
@Observable
final class ArtistEditor {
    let id: String
    private(set) var phase = EditorPhase.loading
    var form: ArtistForm?
    private(set) var isSaving = false
    private(set) var error: String?

    @ObservationIgnored private var original: ArtistForm?
    @ObservationIgnored private let connection: ServerConnection

    init(id: String, connection: ServerConnection) {
        self.id = id
        self.connection = connection
    }

    var hasChanges: Bool {
        guard let form, let original else { return false }
        return form.patch(from: original) != nil
    }

    func load() async {
        do {
            let artist = try await connection.client.getArtist(path: .init(id: id)).ok.body.json
            form = ArtistForm(artist)
            original = form
            phase = .ready
        } catch {
            phase = .failed(String(localized: "Couldn’t load the artist."))
            connection.reportFailure(error)
        }
    }

    func save() async -> Bool {
        guard let form, let original, form.isValid else { return false }
        guard let patch = form.patch(from: original) else { return true }
        isSaving = true
        error = nil
        defer { isSaving = false }
        let client = connection.client
        let id = id
        do {
            _ = try await withExplicitNulls(patch.nulls) {
                try await client.updateArtist(path: .init(id: id), body: .json(patch.body)).ok
            }
            self.original = form
            return true
        } catch {
            self.error = EditErrors.describe(error)
            connection.reportFailure(error)
            return false
        }
    }
}

/// The track sheet: the recording's title, credit, length and MusicBrainz ID.
@Observable
final class RecordingEditor {
    let id: String
    private(set) var phase = EditorPhase.loading
    var form: RecordingForm?
    private(set) var isSaving = false
    private(set) var error: String?

    @ObservationIgnored private var original: RecordingForm?
    @ObservationIgnored private let connection: ServerConnection

    init(id: String, connection: ServerConnection) {
        self.id = id
        self.connection = connection
    }

    var hasChanges: Bool {
        guard let form, let original else { return false }
        return form.patch(from: original) != nil
    }

    func load() async {
        do {
            let recording = try await connection.client.getRecording(path: .init(id: id)).ok.body.json
            form = RecordingForm(recording)
            original = form
            phase = .ready
        } catch {
            phase = .failed(String(localized: "Couldn’t load the track."))
            connection.reportFailure(error)
        }
    }

    func save() async -> Bool {
        guard let form, let original, form.isValid else { return false }
        guard let patch = form.patch(from: original) else { return true }
        isSaving = true
        error = nil
        defer { isSaving = false }
        let client = connection.client
        let id = id
        do {
            _ = try await withExplicitNulls(patch.nulls) {
                try await client.updateRecording(path: .init(id: id), body: .json(patch.body)).ok
            }
            self.original = form
            return true
        } catch {
            self.error = EditErrors.describe(error)
            connection.reportFailure(error)
            return false
        }
    }
}

enum EditErrors {
    /// What the sheet says when Save fails (the server is unreachable, or it refused a
    /// value or a conflicting change).
    static func describe(_ error: any Error) -> String {
        ServerConnection.isTransportFailure(error)
            ? String(localized: "Couldn’t save: the server cannot be reached.")
            : String(localized: "Couldn’t save: the server did not accept the change.")
    }
}
