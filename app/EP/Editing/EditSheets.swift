import AppKit
import EPAPI
import SwiftUI
import UniformTypeIdentifiers

/// The edit sheet for an artist, an album or a track (board 9; decision 6). Saving sends
/// only what changed; `onSaved` reloads what the window shows.
struct EditSheet: View {
    let target: EditTarget
    let onSaved: () -> Void

    var body: some View {
        switch target {
        case .releaseGroup(let id): AlbumEditSheet(id: id, onSaved: onSaved)
        case .artist(let id): ArtistEditSheet(id: id, onSaved: onSaved)
        case .recording(let id): RecordingEditSheet(id: id, onSaved: onSaved)
        }
    }
}

// MARK: - Chrome

/// A sheet's frame: title, body, and the footer "Changes stay in EP — audio files are
/// never rewritten." with Cancel and "Save ⌘S".
struct SheetChrome<Content: View>: View {
    let title: String
    let id: String
    let phase: EditorPhase
    var error: String?
    var saveTitle: LocalizedStringKey = "Save"
    var canSave: Bool
    var isSaving: Bool
    let save: () -> Void
    @ViewBuilder var content: Content

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            Text(title)
                .font(Typography.sheetHeading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 24)
                .padding(.top, 18)
            Group {
                switch phase {
                case .loading:
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity, minHeight: 160)
                case .failed(let message):
                    ColumnMessage(id: id, text: message).frame(minHeight: 160)
                case .ready:
                    content
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 18)
            footer
        }
        .background(Palette.bg)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(id)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if let error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .lineLimit(2)
                    .accessibilityIdentifier("\(id).error")
            } else {
                Text("Changes stay in EP — audio files are never rewritten.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
            }
            Spacer(minLength: 8)
            if isSaving { ProgressView().controlSize(.small) }
            Button("Cancel", role: .cancel) { dismiss() }
                .keyboardShortcut(.cancelAction)
                .accessibilityIdentifier("\(id).cancel")
            Button(action: save) {
                Text(saveTitle) + Text(verbatim: "  ⌘S").foregroundStyle(.secondary)
            }
            .keyboardShortcut("s", modifiers: .command)
            .buttonStyle(.borderedProminent)
            .disabled(!canSave || isSaving)
            .accessibilityLabel(Text(saveTitle))
            .accessibilityIdentifier("\(id).save")
        }
        .padding(.horizontal, 24)
        .frame(height: 56)
        .background(Palette.side)
        .overlay(alignment: .top) { HairlineDivider() }
    }
}

/// A form row: right-aligned label in a 110 pt column, then the control.
struct FormRow<Content: View>: View {
    let label: LocalizedStringKey
    @ViewBuilder var content: Content

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(Typography.body)
                .foregroundStyle(Palette.text2)
                .frame(width: 110, alignment: .trailing)
            content.frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// "release-group · 3c1c…a92e" with Detach (clears the MusicBrainz ID on Save).
struct MusicBrainzRow: View {
    let kind: String
    @Binding var mbid: String?
    let id: String

    var body: some View {
        FormRow(label: "MusicBrainz") {
            if let mbid {
                HStack(spacing: 8) {
                    Text(verbatim: "\(kind) · \(mbid.prefix(4))…\(mbid.suffix(4))")
                        .font(Typography.mono)
                        .foregroundStyle(Palette.text2)
                        .help(mbid)
                        .textSelection(.enabled)
                    Button("Detach") { self.mbid = nil }
                        .controlSize(.small)
                        .help("Forget the MusicBrainz ID (on Save)")
                        .accessibilityIdentifier("\(id).detach")
                }
            } else {
                Text("Not linked").font(Typography.secondary).foregroundStyle(Palette.text3)
            }
        }
    }
}

// MARK: - Album

private struct AlbumEditSheet: View {
    let id: String
    let onSaved: () -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var editor: AlbumEditor?
    @State private var selectedTrack: String?

    var body: some View {
        let editor = editor
        SheetChrome(
            title: String(localized: "Edit Album"), id: "edit.album", phase: editor?.phase ?? .loading,
            error: editor?.error, canSave: editor?.form?.isValid == true && editor?.hasChanges == true,
            isSaving: editor?.isSaving == true, save: save
        ) {
            if let editor { AlbumEditBody(editor: editor, selectedTrack: $selectedTrack) }
        }
        .frame(width: 900)
        .frame(minHeight: 600)
        .task {
            let editor = AlbumEditor(id: id, connection: connection)
            self.editor = editor
            await editor.load()
        }
    }

    private func save() {
        guard let editor else { return }
        Task {
            if await editor.save() {
                onSaved()
                dismiss()
            }
        }
    }
}

private struct AlbumEditBody: View {
    @Bindable var editor: AlbumEditor
    @Binding var selectedTrack: String?

    @State private var dropTargeted = false
    @State private var addingTrack = false
    @State private var newTrack = ""

    var body: some View {
        HStack(alignment: .top, spacing: 24) {
            cover
            VStack(alignment: .leading, spacing: 14) {
                if editor.form != nil { form }
                tracks
            }
        }
    }

    // MARK: Cover

    private var cover: some View {
        VStack(spacing: 10) {
            ZStack(alignment: .bottom) {
                Group {
                    if let cover = editor.newCover, let image = NSImage(data: cover.data) {
                        Image(nsImage: image).resizable().scaledToFill()
                    } else {
                        CoverThumbnail(path: editor.coverURL, size: 180, radius: 6)
                    }
                }
                .frame(width: 180, height: 180)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                Text("Drop image to replace")
                    .font(Typography.meta)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.black.opacity(0.55), in: Capsule())
                    .padding(.bottom, 8)
                    .opacity(dropTargeted ? 1 : 0.85)
            }
            .overlay(
                RoundedRectangle(cornerRadius: 6)
                    .strokeBorder(Palette.accent, style: StrokeStyle(lineWidth: 2, dash: [5, 3]))
                    .padding(-4)
                    .opacity(dropTargeted ? 1 : 0)
            )
            .dropDestination(for: URL.self) { urls, _ in
                guard let url = urls.first else { return false }
                return useImage(at: url)
            } isTargeted: { dropTargeted = $0 }
            .accessibilityElement()
            .accessibilityLabel("Cover — drop an image to replace")
            .accessibilityIdentifier("edit.album.cover")
            Button("Choose image…", action: chooseImage)
                .frame(maxWidth: .infinity)
                .accessibilityIdentifier("edit.album.chooseImage")
        }
        .frame(width: 180)
    }

    private func chooseImage() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .webP]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        _ = useImage(at: url)
    }

    private func useImage(at url: URL) -> Bool {
        // A dropped web link is not an image on this Mac (and must not be fetched here).
        guard url.isFileURL, let type = UTType(filenameExtension: url.pathExtension),
            [UTType.png, .jpeg, .webP].contains(where: type.conforms(to:)),
            let data = try? Data(contentsOf: url)
        else { return false }
        editor.newCover = NewCover(data: data, type: type)
        return true
    }

    // MARK: Form

    @ViewBuilder private var form: some View {
        let form = Binding { editor.form! } set: { editor.form = $0 }
        VStack(alignment: .leading, spacing: 10) {
            FormRow(label: "Title") {
                TextField("Title", text: form.title)
                    .textFieldStyle(.roundedBorder)
                    .labelsHidden()
                    .accessibilityIdentifier("edit.album.title")
            }
            FormRow(label: "Artist credit") {
                CreditEditor(parts: form.credit, id: "edit.album.credit")
            }
            FormRow(label: "Type") {
                HStack(spacing: 12) {
                    Picker("Type", selection: form.type) {
                        ForEach(Components.Schemas.ReleaseGroupType.allCases, id: \.self) { type in
                            Text(type.displayName).tag(type)
                        }
                    }
                    .labelsHidden()
                    .fixedSize()
                    .accessibilityIdentifier("edit.album.type")
                    Text("Date").font(Typography.body).foregroundStyle(Palette.text2)
                    TextField("YYYY-MM-DD", text: form.date)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 110)
                        .foregroundStyle(PartialDate.isValid(form.wrappedValue.date) ? Palette.text : Palette.danger)
                        .accessibilityLabel("Date")
                        .accessibilityIdentifier("edit.album.date")
                }
            }
            MusicBrainzRow(kind: "release-group", mbid: form.mbid, id: "edit.album")
        }
    }

    // MARK: Tracks

    private var tracks: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Tracks · drag to reorder").sectionLabelStyle()
                Spacer()
                Button("+ Track") { addingTrack = true }
                    .controlSize(.small)
                    .accessibilityIdentifier("edit.album.addTrack")
            }
            .padding(.horizontal, 10)
            .frame(height: 30)
            .background(Palette.side)
            .overlay(alignment: .bottom) { HairlineDivider() }
            List(selection: $selectedTrack) {
                ForEach(Array(editor.tracks.enumerated()), id: \.element.id) { index, row in
                    TrackEditRowView(row: row, number: number(at: index))
                        .tag(row.id)
                        .listRowSeparator(.hidden)
                }
                .onMove { editor.moveTracks(from: $0, to: $1) }
            }
            .listStyle(.plain)
            .environment(\.defaultMinListRowHeight, RowHeight.editTrack)
            .frame(height: min(CGFloat(max(editor.tracks.count, 3)) * RowHeight.editTrack + 8, 240))
            .accessibilityIdentifier("edit.album.tracks")
            if addingTrack {
                TextField("New track title — Return adds it", text: $newTrack)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        editor.addTrack(newTrack)
                        newTrack = ""
                    }
                    .onExitCommand { addingTrack = false }
                    .padding(8)
                    .accessibilityIdentifier("edit.album.newTrack")
            }
            if let index = editor.tracks.firstIndex(where: { $0.id == selectedTrack }), editor.tracks[index].trackID != nil {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Credit for track \(number(at: index)) — artists with joining text")
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text2)
                    CreditEditor(parts: $editor.tracks[index].credit, id: "edit.album.trackCredit")
                }
                .padding(10)
                .background(Palette.selSoft)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: Radius.largeButton).strokeBorder(Palette.line))
        .clipShape(RoundedRectangle(cornerRadius: Radius.largeButton))
    }

    /// The track's number on its disc, as the order now stands.
    private func number(at index: Int) -> Int {
        let disc = editor.tracks[index].disc
        return editor.tracks[...index].filter { $0.disc == disc }.count
    }
}

private struct TrackEditRowView: View {
    let row: TrackEditRow
    let number: Int

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 10))
                .foregroundStyle(Palette.text3)
                .frame(width: 18)
                .accessibilityHidden(true)
            Text(verbatim: "\(number)")
                .monospacedDigit()
                .foregroundStyle(Palette.text3)
                .frame(width: 26, alignment: .trailing)
            Text(row.title).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
            Text(Credit.rendered(row.credit))
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(LibraryFormat.duration(ms: row.lengthMS) ?? "")
                .font(Typography.meta)
                .monospacedDigit()
                .foregroundStyle(Palette.text3)
                .frame(width: 44, alignment: .trailing)
        }
        .font(Typography.body)
        .frame(height: RowHeight.editTrack)
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("edit.album.track.\(row.id)")
    }
}

// MARK: - Artist

private struct ArtistEditSheet: View {
    let id: String
    let onSaved: () -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var editor: ArtistEditor?

    var body: some View {
        SheetChrome(
            title: String(localized: "Edit Artist"), id: "edit.artist", phase: editor?.phase ?? .loading,
            error: editor?.error, canSave: editor?.form?.isValid == true && editor?.hasChanges == true,
            isSaving: editor?.isSaving == true, save: save
        ) {
            if let editor, editor.form != nil { ArtistFields(editor: editor) }
        }
        .frame(width: 560)
        .task {
            let editor = ArtistEditor(id: id, connection: connection)
            self.editor = editor
            await editor.load()
        }
    }

    private func save() {
        guard let editor else { return }
        Task {
            if await editor.save() {
                onSaved()
                dismiss()
            }
        }
    }
}

private struct ArtistFields: View {
    @Bindable var editor: ArtistEditor

    var body: some View {
        let form = Binding { editor.form! } set: { editor.form = $0 }
        VStack(alignment: .leading, spacing: 10) {
            FormRow(label: "Name") {
                TextField("Name", text: form.name)
                    .textFieldStyle(.roundedBorder).labelsHidden()
                    .accessibilityIdentifier("edit.artist.name")
            }
            FormRow(label: "Sort name") {
                TextField("Sort name", text: form.sortName)
                    .textFieldStyle(.roundedBorder).labelsHidden()
                    .accessibilityIdentifier("edit.artist.sortName")
            }
            FormRow(label: "Disambiguation") {
                TextField("e.g. Venezuelan producer", text: form.disambiguation)
                    .textFieldStyle(.roundedBorder).labelsHidden()
                    .accessibilityIdentifier("edit.artist.disambiguation")
            }
            FormRow(label: "Type") {
                Picker("Type", selection: form.type) {
                    Text("Not set").tag(Components.Schemas.ArtistType?.none)
                    Text("Person").tag(Components.Schemas.ArtistType?.some(.person))
                    Text("Group").tag(Components.Schemas.ArtistType?.some(.group))
                    Text("Other").tag(Components.Schemas.ArtistType?.some(.other))
                }
                .labelsHidden()
                .fixedSize()
                .accessibilityIdentifier("edit.artist.type")
            }
            MusicBrainzRow(kind: "artist", mbid: form.mbid, id: "edit.artist")
        }
    }
}

// MARK: - Track (recording)

private struct RecordingEditSheet: View {
    let id: String
    let onSaved: () -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var editor: RecordingEditor?

    var body: some View {
        SheetChrome(
            title: String(localized: "Edit Track"), id: "edit.track", phase: editor?.phase ?? .loading,
            error: editor?.error, canSave: editor?.form?.isValid == true && editor?.hasChanges == true,
            isSaving: editor?.isSaving == true, save: save
        ) {
            if let editor, editor.form != nil { RecordingFields(editor: editor) }
        }
        .frame(width: 600)
        .task {
            let editor = RecordingEditor(id: id, connection: connection)
            self.editor = editor
            await editor.load()
        }
    }

    private func save() {
        guard let editor else { return }
        Task {
            if await editor.save() {
                onSaved()
                dismiss()
            }
        }
    }
}

private struct RecordingFields: View {
    @Bindable var editor: RecordingEditor

    var body: some View {
        let form = Binding { editor.form! } set: { editor.form = $0 }
        VStack(alignment: .leading, spacing: 10) {
            FormRow(label: "Title") {
                TextField("Title", text: form.title)
                    .textFieldStyle(.roundedBorder).labelsHidden()
                    .accessibilityIdentifier("edit.track.title")
            }
            FormRow(label: "Artist credit") {
                CreditEditor(parts: form.credit, id: "edit.track.credit")
            }
            FormRow(label: "Length") {
                TextField("4:15", text: form.length)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                    .foregroundStyle(LengthText.parse(form.wrappedValue.length) == nil ? Palette.danger : Palette.text)
                    .accessibilityLabel("Length")
                    .accessibilityIdentifier("edit.track.length")
            }
            MusicBrainzRow(kind: "recording", mbid: form.mbid, id: "edit.track")
            Text("A track is its recording: the title, credit and length show on every album and radio excerpt of it.")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
