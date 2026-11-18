import EPAPI
import SwiftUI

/// SOURCES · n in the track panel: each of the recording's sources as a small tree — the
/// file (or range, radio excerpt, link) with its format, then what it came from (the
/// link it was downloaded from, nearest first) and the import that brought it in. The
/// source that plays is marked; a missing file is flagged. Read-only: choosing and adding
/// sources stays in the sources popover (the track's state icon, "Sources…").
struct SourcesSection: View {
    let recordingID: String
    let id: String

    @Environment(ServerConnection.self) private var connection
    @Environment(ImportStore.self) private var imports
    @State private var store: SourcesStore?

    var body: some View {
        DetailSection(title: title, id: "\(id).sources") {
            content
        }
        .task(id: LoadKey(recordingID: recordingID, libraryChanges: imports.libraryChanges)) {
            if store?.recordingID != recordingID {
                store = SourcesStore(recordingID: recordingID, connection: connection)
            }
            store?.load()
        }
    }

    private var title: String {
        guard let store, store.phase == .loaded else { return String(localized: "Sources") }
        return String(localized: "Sources · \(store.sources.count)")
    }

    @ViewBuilder private var content: some View {
        if let store, store.recordingID == recordingID {
            switch store.phase {
            case .failed(let message):
                Text(message).font(Typography.secondary).foregroundStyle(Palette.danger)
            case .loaded where store.sources.isEmpty:
                Text("No sources — metadata only.").font(Typography.secondary).foregroundStyle(Palette.text3)
            default:
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(store.sources, id: \.source.id) { source in
                        SourceTree(source: source, id: "\(id).source.\(source.source.id)")
                    }
                }
            }
        }
    }

    private struct LoadKey: Equatable {
        let recordingID: String
        let libraryChanges: Int
    }
}

/// One source: its line (kind, format, will play / file missing), the location, the
/// file's figures, then its ancestors and the import, each a branch below it.
private struct SourceTree: View {
    let source: Components.Schemas.RecordingSourceRead
    let id: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            TextRow(spacing: 6) {
                Image(systemName: source.symbol)
                    .font(.system(size: 11))
                    .foregroundStyle(source.isMissing ? Palette.danger : Palette.text2)
                    .frame(width: 14)
                    .centeredOnText()
                    .accessibilityHidden(true)
                Text(source.title).font(Typography.name).foregroundStyle(Palette.text)
                if let format = source.formatLine {
                    Text(format).font(Typography.secondary).foregroundStyle(Palette.text2).monospacedDigit()
                }
                Spacer(minLength: 4)
                if source.isMissing {
                    Text("file missing").font(Typography.meta).foregroundStyle(Palette.danger)
                } else if source.willPlay {
                    Text(source.preferred ? "plays · chosen" : "plays")
                        .font(Typography.meta)
                        .foregroundStyle(Palette.accent)
                } else if source.preferred {
                    Text("chosen").font(Typography.meta).foregroundStyle(Palette.text3)
                }
            }
            .lineLimit(1)
            VStack(alignment: .leading, spacing: 3) {
                if !source.location.isEmpty {
                    Text(source.location)
                        .font(source.role == .radioExcerpt ? Typography.secondary : Typography.mono)
                        .foregroundStyle(source.isMissing ? Palette.danger : Palette.text2)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                        .help(source.location)
                }
                if let figures = source.figures {
                    Text(figures).font(Typography.secondary).foregroundStyle(Palette.text3).monospacedDigit()
                }
                ForEach(source.ancestors, id: \.id) { ancestor in
                    Branch(label: String(localized: "from \(ancestor.kind.displayName)"), value: ancestor.locationText)
                }
                if let job = source.importedBy {
                    Branch(
                        label: String(
                            localized:
                                "imported \(job.createdAt.formatted(.dateTime.day().month(.abbreviated).year())) · \(job.kind.displayName)"
                        ), value: job.inputText)
                }
            }
            .padding(.leading, 20)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            source.willPlay ? Palette.selSoft.opacity(0.6) : .clear,
            in: RoundedRectangle(cornerRadius: Radius.rowLarge)
        )
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(id)
    }
}

/// "└ from Bandcamp  https://…": where a source came from, one step up its tree.
private struct Branch: View {
    let label: String
    let value: String

    var body: some View {
        TextRow(spacing: 6) {
            Text(verbatim: "└").font(Typography.secondary).foregroundStyle(Palette.text3)
            Text(label).font(Typography.secondary).foregroundStyle(Palette.text2)
            Text(value)
                .font(Typography.mono)
                .foregroundStyle(Palette.text2)
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
                .help(value)
        }
    }
}

// MARK: - What a source shows

extension Components.Schemas.RecordingSourceRead {
    fileprivate var symbol: String {
        if isMissing { return "exclamationmark.circle" }
        switch role {
        case .file: return "doc"
        case .slice: return "scissors"
        case .radioExcerpt: return "dot.radiowaves.left.and.right"
        case .link: return "link"
        }
    }

    /// "FLAC · 16-bit · 44.1 kHz · stereo · 1,411 kbps".
    var formatLine: String? {
        guard let file else { return nil }
        let container = SourceFormat.container(file.mediaType)
        let codec = (file.codec ?? source.codec).map { $0.uppercased() }
        let name = [container, codec == container ? nil : codec].compactMap(\.self).joined(separator: "/")
        return [
            name.isEmpty ? nil : name,
            file.bitDepth.map { String(localized: "\($0)-bit") },
            file.sampleRate.map(SourceFormat.sampleRate),
            file.channels.map(SourceFormat.channels),
            file.bitRate.map { String(localized: "\(LibraryFormat.count(($0 + 500) / 1000)) kbps") },
        ]
        .compactMap(\.self).joined(separator: " · ")
    }

    /// "4:05 · 28.4 MB"; for a range of a file (or an episode): "whole file 1:00:00 · …".
    var figures: String? {
        let duration = LibraryFormat.duration(ms: file?.durationMs ?? source.durationMs)
        let size = (file?.size ?? source.size).map(SourceFormat.size)
        let parts = [duration, size].compactMap(\.self)
        guard !parts.isEmpty else { return nil }
        let figures = parts.joined(separator: " · ")
        return role == .file ? figures : String(localized: "whole file \(figures)")
    }
}

extension Components.Schemas.SourceRead {
    /// A link's URL, a file's path on this Mac (else the server's).
    fileprivate var locationText: String {
        ImportFormat.abbreviatingHome(hostPath ?? uri ?? "")
    }
}

extension Components.Schemas.SourceImportRead {
    /// The page URL, or the folder (on this Mac when known).
    fileprivate var inputText: String {
        ImportFormat.abbreviatingHome(inputHostPath ?? input)
    }
}

enum SourceFormat {
    /// "FLAC", "MP3", "M4A", "OGG" from a media type.
    static func container(_ mediaType: String) -> String? {
        guard let subtype = mediaType.split(separator: "/").last.map(String.init), !subtype.isEmpty else {
            return nil
        }
        switch subtype.lowercased() {
        case "mpeg", "mp3": return "MP3"
        case "flac", "x-flac": return "FLAC"
        case "mp4", "x-m4a", "m4a", "aac": return "M4A"
        case "wav", "x-wav", "wave": return "WAV"
        case "aiff", "x-aiff": return "AIFF"
        default: return subtype.uppercased()
        }
    }

    /// "44.1 kHz".
    static func sampleRate(_ hertz: Int) -> String {
        let kHz = (Double(hertz) / 1000).formatted(.number.precision(.fractionLength(0...1)))
        return String(localized: "\(kHz) kHz")
    }

    static func channels(_ count: Int) -> String {
        switch count {
        case 1: String(localized: "mono")
        case 2: String(localized: "stereo")
        default: String(localized: "\(count) ch")
        }
    }

    /// "28.4 MB".
    static func size(_ bytes: Int) -> String {
        Int64(bytes).formatted(.byteCount(style: .file))
    }
}
