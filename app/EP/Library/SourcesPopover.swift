import AppKit
import EPAPI
import SwiftUI
import UniformTypeIdentifiers

/// The sources popover (board 5, §3.6; decision 6: the track's state icon or "Sources…"):
/// "Sources for “Jóga”", a radio group of the recording's sources (kind, codec chip,
/// provenance; path, episode range or link), the chosen one on `--selsoft`. Links cannot
/// be the playing source. A missing file offers Locate…; Show in Finder and Add source…
/// at the bottom.
struct SourcesPopover: View {
    let title: String
    /// Kept for the popover's life (a new one handed in on a redraw is dropped).
    @State private var store: SourcesStore
    @State private var adding = false
    @State private var link = ""

    init(title: String, store: SourcesStore) {
        self.title = title
        _store = State(initialValue: store)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Sources for “\(title)”")
                    .font(Typography.panelTitle)
                    .foregroundStyle(Palette.text)
                Text(subtitle)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
            }
            .padding(.horizontal, 14)
            .padding(.top, 14)
            .padding(.bottom, 8)

            options
                .padding(.horizontal, 8)
                .padding(.bottom, 8)

            if let error = store.error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .padding(.horizontal, 14)
                    .padding(.bottom, 8)
                    .accessibilityIdentifier("sources.error")
            }
            if adding { addRow }
            footer
        }
        .frame(width: 450)
        .background(Palette.bg)
        .onAppear { store.load() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("sources.popover")
    }

    private var subtitle: String {
        let count = store.sources.count
        let sources = count == 1 ? String(localized: "1 source") : String(localized: "\(count) sources")
        return String(localized: "Recording · \(sources) · choose which one plays")
    }

    @ViewBuilder private var options: some View {
        switch store.phase {
        case .failed(let message):
            ColumnMessage(id: "sources", text: message) { store.load() }.frame(height: 80)
        case .loaded where store.sources.isEmpty:
            Text("No sources — metadata only.")
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .padding(.horizontal, 6)
        default:
            VStack(spacing: 2) {
                ForEach(store.sources, id: \.source.id) { source in
                    SourceOption(source: source, chosen: source.source.id == store.chosenID, store: store)
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel("Preferred source")
        }
    }

    private var addRow: some View {
        HStack(spacing: 8) {
            TextField("Paste a link", text: $link)
                .textFieldStyle(.roundedBorder)
                .onSubmit(addLink)
                .accessibilityLabel("Link to add")
                .accessibilityIdentifier("sources.add.link")
            Button("Add", action: addLink)
                .disabled(link.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("sources.add.confirm")
            Button("Choose File…", action: chooseFile)
                .accessibilityIdentifier("sources.add.file")
        }
        .controlSize(.small)
        .padding(.horizontal, 14)
        .padding(.bottom, 10)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if store.hasPreference {
                Button("Use Default") { store.choose(nil) }
                    .buttonStyle(.link)
                    .help("Let EP choose: the oldest file that is there, else a radio excerpt")
                    .accessibilityIdentifier("sources.useDefault")
            }
            Spacer(minLength: 0)
            Button("Show in Finder", action: showInFinder)
                .disabled(finderURL == nil)
                .accessibilityIdentifier("sources.showInFinder")
            Button("Add source…") { adding.toggle() }
                .accessibilityIdentifier("sources.add")
        }
        .controlSize(.small)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .overlay(alignment: .top) { HairlineDivider() }
    }

    /// The chosen file (else the first one that is there) on this Mac.
    private var finderURL: URL? {
        let files = store.sources.compactMap(\.file).filter(\.exists)
        let chosen = store.sources.first { $0.source.id == store.chosenID }?.file
        return (chosen?.exists == true ? chosen : files.first)?.hostPath.map { URL(filePath: $0) }
    }

    private func showInFinder() {
        if let url = finderURL { NSWorkspace.shared.activateFileViewerSelecting([url]) }
    }

    private func addLink() {
        store.addLink(link)
        link = ""
        adding = false
    }

    private func chooseFile() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.allowedContentTypes = [.audio]
        panel.message = String(localized: "Choose an audio file of “\(title)”")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        store.addFile(at: url.path(percentEncoded: false))
        adding = false
    }
}

private struct SourceOption: View {
    let source: Components.Schemas.RecordingSourceRead
    let chosen: Bool
    let store: SourcesStore

    private var isLink: Bool { source.role == .link }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                .font(.system(size: 13))
                .foregroundStyle(chosen ? Palette.accent : Palette.text3)
                .padding(.top, 1)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                TextRow(spacing: 6) {
                    Text(source.title).font(Typography.name)
                    if let chip = source.codecChip {
                        Text(chip)
                            .font(Typography.meta)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Palette.chip, in: RoundedRectangle(cornerRadius: Radius.chip))
                    }
                    Text(source.provenance).font(Typography.secondary).foregroundStyle(Palette.text2)
                    if source.role == .radioExcerpt {
                        Text("excerpt").font(Typography.secondary).foregroundStyle(Palette.warn)
                    }
                    if source.isMissing {
                        Text("file missing").font(Typography.secondary).foregroundStyle(Palette.danger)
                    }
                }
                .lineLimit(1)
                Text(source.location)
                    .font(source.role == .radioExcerpt ? Typography.secondary : Typography.mono)
                    .foregroundStyle(Palette.text2)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 0)
            if source.isMissing, let file = source.file {
                Button("Locate…") { locate(file.sourceId) }
                    .controlSize(.small)
                    .accessibilityIdentifier("sources.locate.\(source.source.id)")
            }
            if isLink, let url = source.source.uri.flatMap(URL.init(string:)) {
                Button("Open") { NSWorkspace.shared.open(url) }
                    .controlSize(.small)
                    .accessibilityLabel("Open link")
                    .accessibilityIdentifier("sources.open.\(source.source.id)")
            }
        }
        .foregroundStyle(isLink ? Palette.text2 : Palette.text)
        .padding(10)
        .background(chosen ? Palette.selSoft : .clear, in: RoundedRectangle(cornerRadius: Radius.largeButton))
        .contentShape(Rectangle())
        .onTapGesture {
            if source.playable, !chosen { store.choose(source.source.id) }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(source.title), \(source.provenance), \(source.location)")
        .accessibilityAddTraits(chosen ? [.isButton, .isSelected] : .isButton)
        .accessibilityHint(isLink ? String(localized: "Links cannot play here") : String(localized: "Plays this source"))
        .accessibilityAction(named: "Choose") {
            if source.playable { store.choose(source.source.id) }
        }
        .accessibilityIdentifier("sources.option.\(source.source.id)")
    }

    private func locate(_ fileSourceID: String) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false
        panel.message = String(localized: "Where is this file now?")
        if let path = source.file?.hostPath {
            panel.directoryURL = URL(filePath: path).deletingLastPathComponent()
        }
        guard panel.runModal() == .OK, let url = panel.url else { return }
        store.locate(fileSourceID, at: url.path(percentEncoded: false))
    }
}
