import AppKit
import EPAPI
import SwiftUI
import UniformTypeIdentifiers

/// The Import sheet (design 7.1): paste a link (the server says what it is) or choose or
/// drop a folder; below, every job — running ones with their stages and log, the rest
/// with what they are waiting for and their actions. Imports keep running after it closes.
struct ImportSheet: View {
    /// A link to start with (⌘V, a link-only track's Import…).
    let link: String?
    /// Whether the link field takes the keyboard (not when opened for the jobs, ⇧⌘I:
    /// then Return reviews the newest job waiting for a decision).
    var focusLink = true
    /// Opens the review sheet of a job (replaces this sheet).
    let review: (String) -> Void
    /// Shows an album already in the library (a release conflict's "Show existing").
    let showExisting: (EntityRef) -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(ImportStore.self) private var imports
    @Environment(\.dismiss) private var dismiss
    @State private var draft: ImportDraft?
    @State private var dropTargeted = false
    @FocusState private var fieldFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            if let draft { start(draft) }
            HairlineDivider()
            jobs
            footer
        }
        .frame(width: 1000, height: 660)
        .background(Palette.bg)
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: Radius.sheet)
                    .strokeBorder(Palette.accent, style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
                    .padding(4)
                    .allowsHitTesting(false)
            }
        }
        .onDrop(of: [.fileURL, .url], isTargeted: $dropTargeted, perform: drop)
        .onAppear {
            if draft == nil { draft = ImportDraft(connection: connection, text: link ?? "") }
            imports.queueShown(true)
            fieldFocused = focusLink
        }
        .onDisappear { imports.queueShown(false) }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("import.sheet")
    }

    // MARK: - Start

    private func start(_ draft: ImportDraft) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Import").font(Typography.sheetHeading)
            HStack(spacing: 8) {
                LinkField(draft: draft, focused: $fieldFocused, submit: { startDraft(draft) })
                Button {
                    startDraft(draft)
                } label: {
                    Text("Import ↩").frame(height: Metrics.largeButtonHeight - 12).padding(.horizontal, 6)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(!draft.canStart)
                .accessibilityLabel("Import")
                .accessibilityIdentifier("import.start")
                Button {
                    chooseFolder(draft)
                } label: {
                    Text("Choose folder…").frame(height: Metrics.largeButtonHeight - 12).padding(.horizontal, 4)
                }
                .controlSize(.large)
                .disabled(draft.isStarting)
                .accessibilityIdentifier("import.chooseFolder")
            }
            if let error = draft.error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("import.error")
            } else {
                Text(
                    "Paste a Bandcamp album, YouTube video or playlist, or NTS episode — or drop a folder anywhere on this sheet. Imports keep running after you close it."
                )
                .font(Typography.secondary)
                .foregroundStyle(Palette.text2)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 24)
        .padding(.vertical, 20)
    }

    private func startDraft(_ draft: ImportDraft) {
        guard draft.canStart else { return }
        Task {
            if let job = await draft.start() { imports.started(job) }
        }
    }

    private func chooseFolder(_ draft: ImportDraft) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = String(localized: "Import")
        panel.message = String(localized: "Choose a folder of audio files. It is imported in place, not copied.")
        if case .connected(let server) = connection.state,
            let root = server.mediaRoots.lazy.compactMap({ $0.hostPath ?? $0.path }).first(where: {
                FileManager.default.fileExists(atPath: $0)
            })
        {
            panel.directoryURL = URL(fileURLWithPath: root)
        }
        let window = NSApp.keyWindow
        let handle = { (response: NSApplication.ModalResponse) in
            guard response == .OK, let url = panel.url else { return }
            startFolder(url, draft: draft)
        }
        if let window {
            panel.beginSheetModal(for: window, completionHandler: handle)
        } else {
            handle(panel.runModal())
        }
    }

    private func startFolder(_ url: URL, draft: ImportDraft) {
        Task {
            if let job = await draft.startFolder(path: url.path) { imports.started(job) }
        }
    }

    /// A folder dropped anywhere on the sheet is imported; a web link fills the field.
    private func drop(_ providers: [NSItemProvider]) -> Bool {
        guard let provider = providers.first, let draft else { return false }
        _ = provider.loadObject(ofClass: URL.self) { url, _ in
            guard let url else { return }
            Task { @MainActor in
                if url.isFileURL {
                    var isDirectory: ObjCBool = false
                    if FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory),
                        isDirectory.boolValue
                    {
                        startFolder(url, draft: draft)
                    }
                } else {
                    draft.update(url.absoluteString)
                }
            }
        }
        return true
    }

    // MARK: - Jobs

    private var jobs: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text("Jobs").sectionLabelStyle()
                Spacer()
                if let watching = imports.summary?.watching, watching > 0 {
                    Text(watching == 1 ? "Watching 1 folder" : "Watching \(watching) folders")
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text3)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 14)
            .padding(.bottom, 8)
            if let error = imports.actionError {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .padding(.horizontal, 24)
                    .padding(.bottom, 6)
                    .accessibilityIdentifier("import.actionError")
            }
            ScrollView {
                LazyVStack(spacing: 0) {
                    let running = imports.jobs.filter { ImportStore.isRunning($0.state) }
                    ForEach(running, id: \.id) { job in
                        ActiveJobCard(job: job)
                            .padding(.horizontal, 16)
                            .padding(.bottom, 8)
                    }
                    ForEach(imports.jobs.filter { !ImportStore.isRunning($0.state) }, id: \.id) { job in
                        JobRow(
                            job: job, reviewIsDefault: !fieldFocused && job.id == imports.firstToReview?.id,
                            review: { review(job.id) }, showExisting: showExisting)
                            .padding(.horizontal, 16)
                    }
                    if imports.jobs.isEmpty {
                        Text("No imports yet.")
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.text3)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    }
                }
                .padding(.bottom, 12)
            }
            .accessibilityIdentifier("import.jobs")
        }
        .frame(maxHeight: .infinity, alignment: .top)
    }

    private var footer: some View {
        HStack {
            Spacer()
            Button("Done") { dismiss() }
                .keyboardShortcut(.cancelAction)
                .accessibilityIdentifier("import.done")
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(Palette.side)
        .overlay(alignment: .top) { HairlineDivider() }
    }
}

/// The link field: 40 pt, radius 8, 2 pt `--sel` border while focused, 14 pt text, with
/// the detected-kind pill inside on the right.
private struct LinkField: View {
    let draft: ImportDraft
    var focused: FocusState<Bool>.Binding
    let submit: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            TextField(
                "Link or folder", text: Binding(get: { draft.text }, set: { draft.update($0) }),
                prompt: Text("https://…")
            )
            .textFieldStyle(.plain)
            .font(.system(size: 14))
            .focused(focused)
            .onSubmit(submit)
            .accessibilityLabel("Link to import")
            .accessibilityIdentifier("import.link")
            if draft.detected == .checking {
                ProgressView().controlSize(.small)
            } else if let pill = draft.pill {
                Text(pill)
                    .font(Typography.secondary)
                    .foregroundStyle(isImportable ? Palette.text : Palette.text2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(isImportable ? Palette.selSoft : Palette.chip, in: Capsule())
                    .accessibilityIdentifier("import.kind")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: Metrics.largeButtonHeight)
        .background(Palette.field, in: RoundedRectangle(cornerRadius: Radius.largeButton))
        .overlay(
            RoundedRectangle(cornerRadius: Radius.largeButton)
                .strokeBorder(
                    focused.wrappedValue ? Palette.sel : Palette.line,
                    lineWidth: focused.wrappedValue ? 2 : Metrics.hairline)
        )
        .accessibilityElement(children: .contain)
    }

    private var isImportable: Bool {
        switch draft.detected {
        case .link, .folder: true
        default: false
        }
    }
}

/// A running job (padding 12, radius 10, `--side`): cover, title, source and start time,
/// Cancel; the four stages; the log.
private struct ActiveJobCard: View {
    let job: ImportStore.Job

    @Environment(ImportStore.self) private var imports

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                CoverThumbnail(path: job.coverUrl, size: 36, radius: Radius.cover)
                VStack(alignment: .leading, spacing: 1) {
                    Text(job.displayTitle).font(Typography.name).lineLimit(1)
                    Text("\(job.sourceLabel) · started \(job.createdAt.formatted(date: .omitted, time: .shortened))")
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text2)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if job.actions.contains(.cancel) {
                    Button("Cancel") { Task { await imports.perform(.cancel, on: job.id) } }
                        .accessibilityLabel("Cancel import of \(job.displayTitle)")
                        .accessibilityIdentifier("import.job.\(job.id).cancel")
                }
            }
            StageStrip(progress: job.progress)
            LogBox(entries: imports.logs[job.id] ?? [], id: "import.job.\(job.id).log")
        }
        .padding(12)
        .background(Palette.side, in: RoundedRectangle(cornerRadius: Radius.card))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("import.job.\(job.id)")
    }
}

/// Fetching metadata ✓ · Matching ✓ · Downloading · 3 of 5 · 62% · Applying.
private struct StageStrip: View {
    let progress: Components.Schemas.ProgressRead

    private static let steps: [Components.Schemas.ImportStep] = [.metadata, .matching, .downloading, .applying]

    var body: some View {
        let current = progress.step.flatMap { Self.steps.firstIndex(of: $0) } ?? 0
        HStack(alignment: .top, spacing: 8) {
            ForEach(Array(Self.steps.enumerated()), id: \.offset) { index, step in
                VStack(alignment: .leading, spacing: 4) {
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            Capsule().fill(index < current ? Palette.text2 : Palette.line)
                            if index == current {
                                Capsule()
                                    .fill(Palette.accent)
                                    .frame(width: geometry.size.width * (progress.fraction ?? 0))
                            }
                        }
                    }
                    .frame(height: 4)
                    Text(label(step, index: index, current: current))
                        .font(index == current ? Typography.subheading : Typography.secondary)
                        .foregroundStyle(index > current ? Palette.text3 : Palette.text2)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("import.stages")
    }

    private func label(_ step: Components.Schemas.ImportStep, index: Int, current: Int) -> String {
        if index < current { return "✓ \(step.title)" }
        guard index == current else { return step.title }
        var parts = [step.title]
        if let total = progress.total, total > 0 {
            parts.append(String(localized: "\(min(progress.done + 1, total)) of \(total)"))
        }
        if let fraction = progress.fraction { parts.append("\(Int(fraction * 100))%") }
        return parts.joined(separator: " · ")
    }
}

/// What was answered in review, for a job queued again with the answers (not drawn in
/// the design; shown above the log of an expanded row): "Subject → answer" per line.
private struct AnswerList: View {
    let issues: [Components.Schemas.Issue]
    let id: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(issues.count == 1 ? "1 answer given" : "\(issues.count) answers given")
                .font(Typography.subheading)
                .foregroundStyle(Palette.text2)
            ForEach(issues, id: \.id) { issue in
                Text("\(issue.subject.label) → \(ReviewModel.answerTitle(for: issue) ?? "")")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
                    .lineLimit(1)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier(id)
    }
}

/// The log: mono 11 pt, line height 1.5, as high as its lines up to `maxHeight` (64 on
/// the running job's card; more in an expanded row, where a failure's lines are read),
/// then it scrolls, newest at the bottom.
private struct LogBox: View {
    let entries: [Components.Schemas.LogEntry]
    let id: String
    var maxHeight: CGFloat = 64

    /// The lines' height, measured: a ScrollView in a lazy stack has no height of its own.
    @State private var contentHeight: CGFloat = 0

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(entries.enumerated()), id: \.offset) { index, entry in
                        Text("\(ImportFormat.clock(entry.at))  \(entry.message)")
                            .foregroundStyle(entry.level == .error ? Palette.danger : Palette.text2)
                            .id(index)
                    }
                    if entries.isEmpty { Text("No log yet.").foregroundStyle(Palette.text3) }
                }
                .font(Typography.mono)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .fixedSize(horizontal: false, vertical: true)
                .onGeometryChange(for: CGFloat.self, of: \.size.height) { contentHeight = $0 }
            }
            .frame(height: min(max(contentHeight, 28), maxHeight))
            .background(Palette.bg, in: RoundedRectangle(cornerRadius: Radius.button))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.button).strokeBorder(Palette.line, lineWidth: Metrics.hairline)
            )
            .onChange(of: entries.count, initial: true) { _, count in
                if count > 0 { proxy.scrollTo(count - 1, anchor: .bottom) }
            }
        }
        .accessibilityLabel("Log")
        .accessibilityIdentifier(id)
    }
}

/// A job row (min height 48, grid `22px 1fr 180px 130px auto`): state icon, title over
/// detail, source, time, actions; a click on the title shows the log.
private struct JobRow: View {
    let job: ImportStore.Job
    /// Return reviews this job (the newest waiting, while the link field has no focus).
    let reviewIsDefault: Bool
    let review: () -> Void
    let showExisting: (EntityRef) -> Void

    @Environment(ImportStore.self) private var imports

    private var isExpanded: Bool { imports.expanded.contains(job.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 12) {
                stateIcon.frame(width: 22)
                Button {
                    imports.toggleLog(job.id)
                } label: {
                    HStack(spacing: 4) {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(job.displayTitle).font(Typography.name).lineLimit(1)
                            Text(detail.text)
                                .font(Typography.secondary)
                                .foregroundStyle(detail.color)
                                .lineLimit(2)
                                .accessibilityIdentifier("import.job.\(job.id).detail")
                        }
                        Image(systemName: isExpanded ? "chevron.down" : "chevron.right")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(Palette.text3)
                            .accessibilityHidden(true)
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(job.displayTitle), \(detail.text)")
                .accessibilityHint(isExpanded ? "Hides the log" : "Shows the log")
                .accessibilityIdentifier("import.job.\(job.id).toggleLog")
                Text(job.sourceLabel)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text2)
                    .lineLimit(1)
                    .frame(width: 170, alignment: .leading)
                Text(ImportFormat.time(job.createdAt))
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    .lineLimit(1)
                    .frame(width: 90, alignment: .leading)
                actions
            }
            if isExpanded {
                VStack(alignment: .leading, spacing: 6) {
                    if ImportStore.isActive(job.state), let answered = imports.answered[job.id], !answered.isEmpty {
                        AnswerList(issues: answered, id: "import.job.\(job.id).answers")
                    }
                    LogBox(entries: imports.logs[job.id] ?? [], id: "import.job.\(job.id).log", maxHeight: 180)
                }
                .padding(.leading, 34)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .frame(minHeight: RowHeight.jobMin)
        .overlay(alignment: .bottom) { HairlineDivider() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("import.job.\(job.id)")
    }

    @ViewBuilder private var actions: some View {
        HStack(spacing: 6) {
            if job.actions.contains(.review) {
                Button("Review", action: review)
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(reviewIsDefault ? .defaultAction : nil)
                    .accessibilityLabel("Review \(job.displayTitle)")
                    .accessibilityIdentifier("import.job.\(job.id).review")
            }
            if let existing = job.existing {
                Button("Show existing") { showExisting(EntityRef(type: existing._type, id: existing.id)) }
                    .accessibilityIdentifier("import.job.\(job.id).showExisting")
            }
            // Decision 11: Retry and Dismiss on a job that failed or waits; a done job
            // can be retried through the API, but the row does not offer it.
            if job.actions.contains(.retry), job.state != .done {
                Button(job.counts.failed > 0 ? "Retry \(job.counts.failed)" : "Retry") {
                    Task { await imports.perform(.retry, on: job.id) }
                }
                .accessibilityLabel("Retry \(job.displayTitle)")
                .accessibilityIdentifier("import.job.\(job.id).retry")
            }
            if job.actions.contains(.dismiss) {
                Button("Dismiss") { Task { await imports.perform(.dismiss, on: job.id) } }
                    .accessibilityLabel("Dismiss \(job.displayTitle)")
                    .accessibilityIdentifier("import.job.\(job.id).dismiss")
            }
            if job.actions.contains(.cancel) {
                Button("Cancel") { Task { await imports.perform(.cancel, on: job.id) } }
                    .accessibilityLabel("Cancel import of \(job.displayTitle)")
                    .accessibilityIdentifier("import.job.\(job.id).cancel")
            }
        }
        .fixedSize()
    }

    @ViewBuilder private var stateIcon: some View {
        switch job.state.known {
        case .needsReview where job.onlyFailures, .failed:
            Image(systemName: "xmark.circle").foregroundStyle(Palette.danger)
                .accessibilityLabel("Failed")
        case .needsReview:
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(Palette.warn)
                .accessibilityLabel("Needs review")
        case .queued:
            Image(systemName: "clock").foregroundStyle(Palette.text3)
                .accessibilityLabel("Waiting")
        case .done:
            Image(systemName: "checkmark").foregroundStyle(Palette.text3)
                .accessibilityLabel("Done")
        case nil:  // a state a newer server added
            Image(systemName: "questionmark.circle").foregroundStyle(Palette.text3)
                .accessibilityLabel(job.state.rawValue)
        default:
            ProgressView().controlSize(.mini)
        }
    }

    /// The second line, by state (design 7.1's table).
    private var detail: (text: String, color: Color) {
        switch job.state.known {
        case .needsReview where job.onlyFailures:
            let message = job.firstIssue?.message ?? String(localized: "Some items failed")
            let imported = job.created.tracks
            let parts = [
                job.counts.tracks > 0 ? "\(message) (\(job.counts.failed) of \(job.counts.tracks))." : "\(message).",
                imported > 0 ? String(localized: "\(imported) imported.") : nil,
            ]
            return (parts.compactMap { $0 }.joined(separator: " "), Palette.danger)
        case .needsReview:
            let open = job.openIssues
            let text =
                open == 1
                ? String(localized: "Needs review · 1 question") : String(localized: "Needs review · \(open) questions")
            if job.firstIssue?.kind.known == .releaseConflict, let message = job.firstIssue?.message {
                return (message, Palette.danger)
            }
            return (text, Palette.warn)
        case .failed:
            return (job.error ?? job.firstIssue?.message ?? String(localized: "Failed"), Palette.danger)
        case .queued:
            if let ahead = job.waitingFor.flatMap(imports.job(id:)) {
                return (String(localized: "Waiting · starts after \(ahead.title ?? ahead.displayTitle)"), Palette.text2)
            }
            return (String(localized: "Waiting"), Palette.text2)
        case .done:
            return (job.doneDetail, Palette.text2)
        case nil:
            return (job.error ?? job.firstIssue?.message ?? job.state.rawValue, Palette.text2)
        default:
            return (String(localized: "Running"), Palette.text2)
        }
    }
}
