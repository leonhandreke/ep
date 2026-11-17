import EPAPI
import SwiftUI

/// A sheet over a main window: editing, creating by hand, or adding from MusicBrainz.
/// `done` gets what was created (the note editor links to it; elsewhere the window shows
/// it).
struct WindowSheet: Identifiable {
    enum Kind {
        case edit(EditTarget)
        /// Create by hand: the kinds offered (one, or album and recording for the note's
        /// "New album or recording…"), a name to start with, a credit to start with.
        case create(kinds: [Creator.Kind], name: String, credit: [CreditPart])
        case musicBrainz(query: String)
        /// The import sheet (start and jobs), with a link to start from; the link field
        /// takes the keyboard unless the sheet was opened for its jobs (⇧⌘I).
        case importer(link: String?, focusLink: Bool = true)
        /// The review sheet of an import job.
        case review(jobID: String)
    }

    let id = UUID()
    var kind: Kind
    var done: ((CreatedEntity) -> Void)?
}

extension EnvironmentValues {
    /// Presents a sheet over this main window (set by `MainWindow`).
    @Entry var presentSheet: ((WindowSheet) -> Void)?
}

// MARK: - Create by hand

struct CreateSheet: View {
    let kinds: [Creator.Kind]
    let name: String
    let credit: [CreditPart]
    let done: (CreatedEntity) -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var creator: Creator?

    var body: some View {
        SheetChrome(
            title: title, id: "create", phase: creator == nil ? .loading : .ready, error: creator?.error,
            saveTitle: "Add", canSave: creator?.isValid == true, isSaving: creator?.isSaving == true, save: save
        ) {
            if let creator { CreateFields(creator: creator, kinds: kinds) }
        }
        .frame(width: 600)
        .onAppear {
            guard creator == nil else { return }
            creator = Creator(kind: kinds.first ?? .artist, name: name, credit: credit, connection: connection)
        }
    }

    private var title: String {
        kinds.count == 1
            ? String(localized: "New \(kinds[0].title)") : String(localized: "New Album or Recording")
    }

    private func save() {
        guard let creator else { return }
        Task {
            guard let created = await creator.create() else { return }
            done(created)
            dismiss()
        }
    }
}

private struct CreateFields: View {
    @Bindable var creator: Creator
    let kinds: [Creator.Kind]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if kinds.count > 1 {
                FormRow(label: "Kind") {
                    Picker("Kind", selection: $creator.kind) {
                        ForEach(kinds) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    .accessibilityIdentifier("create.kind")
                }
            }
            FormRow(label: creator.kind == .artist ? "Name" : "Title") {
                TextField("", text: $creator.name)
                    .textFieldStyle(.roundedBorder).labelsHidden()
                    .accessibilityLabel(creator.kind == .artist ? "Name" : "Title")
                    .accessibilityIdentifier("create.name")
            }
            switch creator.kind {
            case .artist:
                FormRow(label: "Sort name") {
                    TextField("Same as the name", text: $creator.sortName)
                        .textFieldStyle(.roundedBorder).labelsHidden()
                        .accessibilityIdentifier("create.sortName")
                }
                FormRow(label: "Disambiguation") {
                    TextField("", text: $creator.disambiguation)
                        .textFieldStyle(.roundedBorder).labelsHidden()
                        .accessibilityIdentifier("create.disambiguation")
                }
            case .album:
                FormRow(label: "Artist credit") { CreditEditor(parts: $creator.credit, id: "create.credit") }
                FormRow(label: "Type") {
                    HStack(spacing: 12) {
                        Picker("Type", selection: $creator.albumType) {
                            ForEach(Components.Schemas.ReleaseGroupType.allCases, id: \.self) {
                                Text($0.displayName).tag($0)
                            }
                        }
                        .labelsHidden()
                        .fixedSize()
                        .accessibilityIdentifier("create.type")
                        Text("Date").font(Typography.body).foregroundStyle(Palette.text2)
                        TextField("YYYY-MM-DD", text: $creator.date)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 110)
                            .accessibilityIdentifier("create.date")
                    }
                }
            case .recording:
                FormRow(label: "Artist credit") { CreditEditor(parts: $creator.credit, id: "create.credit") }
                FormRow(label: "Length") {
                    TextField("4:15", text: $creator.length)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 90)
                        .accessibilityIdentifier("create.length")
                }
            }
        }
    }
}

// MARK: - MusicBrainz

struct MusicBrainzSheet: View {
    let query: String
    let done: (CreatedEntity) -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(\.dismiss) private var dismiss
    @State private var search: MusicBrainzSearch?
    @State private var selection: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Search MusicBrainz").font(Typography.sheetHeading)
            if let search { content(search) }
            HStack {
                if let error = search?.error {
                    Text(error).font(Typography.secondary).foregroundStyle(Palette.danger)
                } else {
                    Text("Adds the metadata only — no audio.").font(Typography.secondary).foregroundStyle(Palette.text2)
                }
                Spacer()
                if search?.isImporting == true { ProgressView().controlSize(.small) }
                Button("Cancel", role: .cancel) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                    .accessibilityIdentifier("musicBrainz.cancel")
                Button("Add to Library", action: add)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                    .disabled(selection == nil || search?.isImporting == true)
                    .accessibilityIdentifier("musicBrainz.add")
            }
        }
        .padding(24)
        .frame(width: 620, height: 520)
        .onAppear {
            guard search == nil else { return }
            let store = MusicBrainzSearch(query: query, connection: connection)
            search = store
            Task { await store.search() }
        }
        .accessibilityIdentifier("musicBrainz.sheet")
    }

    @ViewBuilder private func content(_ search: MusicBrainzSearch) -> some View {
        @Bindable var search = search
        HStack(spacing: 8) {
            Picker("Type", selection: $search.type) {
                Text("Artist").tag(Components.Schemas.MusicBrainzSearchType.artist)
                Text("Release").tag(Components.Schemas.MusicBrainzSearchType.release)
                Text("Recording").tag(Components.Schemas.MusicBrainzSearchType.recording)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .onChange(of: search.type) { _, _ in Task { await search.search() } }
            .accessibilityIdentifier("musicBrainz.type")
            TextField("Search", text: $search.query)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await search.search() } }
                .accessibilityIdentifier("musicBrainz.query")
            if search.isSearching { ProgressView().controlSize(.small) }
        }
        List(selection: $selection) {
            ForEach(search.hits, id: \.mbid) { hit in
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(hit.title).font(Typography.name)
                        if !hit.disambiguation.isEmpty {
                            Text(hit.disambiguation).font(Typography.secondary).foregroundStyle(Palette.text3)
                        }
                    }
                    Text(
                        [hit.artistCredit, hit.date].compactMap { $0?.isEmpty == false ? $0 : nil }.joined(
                            separator: " · ")
                    )
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
                }
                .lineLimit(1)
                .padding(.vertical, 3)
                .tag(hit.mbid)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("musicBrainz.row.\(hit.mbid)")
            }
        }
        .listStyle(.bordered)
        .contextMenu(forSelectionType: String.self) { _ in
        } primaryAction: { _ in
            add()
        }
        .overlay {
            if search.hits.isEmpty, !search.isSearching, search.searched != nil {
                Text("Nothing found on MusicBrainz.").font(Typography.secondary).foregroundStyle(Palette.text3)
            }
        }
    }

    private func add() {
        guard let search, let hit = search.hits.first(where: { $0.mbid == selection }) else { return }
        Task {
            guard let created = await search.add(hit) else { return }
            done(created)
            dismiss()
        }
    }
}
