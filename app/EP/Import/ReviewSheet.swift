import EPAPI
import SwiftUI

/// Review uncertain matches (design 7.2, decision 10): questions on the left, the selected
/// one on the right with the incoming item next to the candidate and its options as a
/// radio group. Driven by the keyboard (`ReviewModel`); defaults are preselected, so
/// Confirm all (⌘↩) is enough when they are right.
struct ReviewSheet: View {
    let jobID: String
    /// After Confirm: the job runs again.
    let confirmed: (Components.Schemas.ImportJobRead) -> Void

    @Environment(ServerConnection.self) private var connection
    @Environment(Player.self) private var player
    @Environment(\.dismiss) private var dismiss
    @State private var model: ReviewModel?
    @State private var preview = ReviewPreview()
    @State private var searching = false
    @FocusState private var keyboardFocus: Bool

    var body: some View {
        VStack(spacing: 0) {
            if let model {
                switch model.phase {
                case .loading:
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity, maxHeight: .infinity)
                case .failed(let message):
                    ColumnMessage(id: "review.message", text: message).frame(maxHeight: .infinity)
                case .ready:
                    header(model)
                    HairlineDivider()
                    HStack(spacing: 0) {
                        QuestionList(model: model)
                            .frame(width: 400)
                            .background(Palette.side)
                        HairlineDivider(axis: .vertical)
                        if let row = model.selected {
                            QuestionDetail(
                                model: model, row: row, searching: $searching, keyboardFocus: $keyboardFocus,
                                preview: preview, togglePreview: { togglePreview(row) })
                        } else {
                            Text("Nothing to review.").foregroundStyle(Palette.text3)
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                    }
                }
            }
            footer
        }
        .frame(width: 1040, height: 700)
        .background(Palette.bg)
        .focusable()
        .focusEffectDisabled()
        .focused($keyboardFocus)
        .onKeyPress(phases: .down) { press in handle(press) }
        // Another question (a click, ↑↓ is the field's own while it is open): the search
        // belongs to the one it was opened for, and the keys come back to the sheet.
        .onChange(of: model?.selectedID) {
            preview.stop()
            if searching {
                searching = false
                keyboardFocus = true
            }
        }
        .task {
            guard model == nil else { return }
            let model = ReviewModel(jobID: jobID, connection: connection)
            self.model = model
            await model.load()
            keyboardFocus = true
        }
        .onDisappear { preview.stop() }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("review.sheet")
    }

    /// Space: the selected question's incoming track plays, or stops. The app's player
    /// pauses meanwhile.
    private func togglePreview(_ row: ReviewModel.Row) {
        guard let source = row.preview else { return }
        if player.isPlaying { player.pause() }
        preview.toggle(
            source, rowID: row.id,
            url: ReviewPreview.url(server: connection.serverURL, jobID: jobID, trackKey: source.trackKey))
    }

    private func handle(_ press: KeyPress) -> KeyPress.Result {
        guard let model, model.phase == .ready, !searching else { return .ignored }
        guard press.modifiers.isDisjoint(with: [.command, .option, .control]) else { return .ignored }
        switch press.key {
        case .upArrow: model.move(-1)
        case .downArrow: model.move(1)
        case .return: model.acceptAndNext()
        case .space:
            guard let row = model.selected, row.preview != nil else { return .ignored }
            togglePreview(row)
        default:
            if press.characters == "/", model.searchType != nil {
                searching = true
                return .handled
            }
            return model.press(press.characters) ? .handled : .ignored
        }
        return .handled
    }

    private func header(_ model: ReviewModel) -> some View {
        HStack(spacing: 12) {
            CoverThumbnail(path: model.job?.coverUrl, size: 36, radius: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text("Review import — \(model.job.map { ImportStore.Job($0).displayTitle } ?? "")")
                    .font(Typography.reviewTitle)
                    .lineLimit(1)
                    .accessibilityIdentifier("review.title")
                let headline = model.headline
                Group {
                    if let decisions = headline.decisions {
                        Text("\(headline.items) · \(Text(decisions).bold().foregroundStyle(Palette.warn))")
                    } else {
                        Text(headline.items)
                    }
                }
                .font(Typography.secondary)
                .foregroundStyle(Palette.text2)
                .accessibilityIdentifier("review.headline")
            }
            Spacer(minLength: 12)
            Picker(
                "Show",
                selection: Binding(get: { model.filter }, set: { model.filter = $0 })
            ) {
                Text("Uncertain \(model.openCount)").tag(ReviewModel.Filter.uncertain)
                Text("All \(model.rows.count)").tag(ReviewModel.Filter.all)
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .accessibilityLabel("Show uncertain or all questions")
            .accessibilityIdentifier("review.filter")
        }
        .padding(.horizontal, 20)
        .frame(height: 64)
    }

    private var footer: some View {
        HStack(spacing: 14) {
            Group {
                Text("↑ ↓ question")
                Text("1–9 candidate")
                Text("↩ accept & next")
                Text("/ search library")
                // Only where the job's files are there to preview.
                if model?.rows.contains(where: { $0.preview != nil }) == true {
                    Text("space preview audio")
                }
            }
            .font(Typography.secondary)
            .foregroundStyle(Palette.text2)
            if let message = preview.message {
                Text(message)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .lineLimit(1)
                    .accessibilityIdentifier("review.previewError")
            }
            if let error = model?.error {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
                    .lineLimit(2)
                    .accessibilityIdentifier("review.error")
            }
            Spacer(minLength: 8)
            if model?.isSubmitting == true { ProgressView().controlSize(.small) }
            Button("Later") { dismiss() }
                // Escape closes the library search first, not the sheet.
                .keyboardShortcut(searching ? nil : .cancelAction)
                .accessibilityIdentifier("review.later")
            Button(confirmTitle, action: confirm)
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(model?.canConfirm != true)
                .accessibilityIdentifier("review.confirm")
        }
        .padding(.horizontal, 20)
        .frame(height: 56)
        .background(Palette.side)
        .overlay(alignment: .top) { HairlineDivider() }
    }

    private var confirmTitle: String {
        let count = model?.answers.count ?? 0
        return count > 0 ? String(localized: "Confirm \(count) ⌘↩") : String(localized: "Confirm ⌘↩")
    }

    private func confirm() {
        guard let model else { return }
        Task {
            guard let job = await model.confirm() else { return }
            confirmed(job)
            dismiss()
        }
    }
}

/// The left pane: one row per question (padding 9 10, radius 8, grid `52px 1fr`): time,
/// "Artist — Title", the question, and the current pick as an outlined pill with its score.
private struct QuestionList: View {
    let model: ReviewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 2) {
                    ForEach(model.visibleRows) { row in
                        QuestionRow(
                            row: row, pick: model.pick(for: row), selected: row.id == model.selectedID,
                            accepted: model.accepted.contains(row.id)
                        )
                        .id(row.id)
                        .onTapGesture { model.select(row.id) }
                    }
                }
                .padding(8)
            }
            .onChange(of: model.selectedID) { _, id in
                guard let id else { return }
                withAnimation(.easeOut(duration: 0.12)) { proxy.scrollTo(id) }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Questions")
        .accessibilityIdentifier("review.questions")
    }
}

private struct QuestionRow: View {
    let row: ReviewModel.Row
    let pick: ReviewModel.Option?
    let selected: Bool
    let accepted: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Text(row.time.map { (row.timeIsEstimate ? "~" : "") + $0 } ?? "")
                .font(Typography.body.monospacedDigit())
                .italic(row.timeIsEstimate)
                // 52 pt in the design, which has no episode past an hour: "~1:30:03" needs more.
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(width: 62, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                Text(row.title).font(Typography.name).lineLimit(1)
                Text(row.question).font(Typography.secondary).lineLimit(2)
                    .foregroundStyle(selected ? Palette.selText.opacity(0.9) : Palette.text2)
                if let pick {
                    HStack(spacing: 6) {
                        Text(pick.title)
                            .font(Typography.secondary)
                            .lineLimit(1)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 1)
                            .overlay(Capsule().strokeBorder(selected ? Palette.selText : Palette.line, lineWidth: 1))
                        if let score = pick.score {
                            Text(ImportFormat.score(score)).font(Typography.secondary.monospacedDigit())
                        }
                        if accepted {
                            Image(systemName: "checkmark").font(.system(size: 10, weight: .bold))
                                .accessibilityLabel("Accepted")
                        }
                    }
                    .foregroundStyle(selected ? Palette.selText : Palette.text2)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(selected ? Palette.selText : Palette.text)
        .padding(.horizontal, 10)
        .padding(.vertical, 9)
        .background(selected ? Palette.sel : .clear, in: RoundedRectangle(cornerRadius: Radius.largeButton))
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
        .accessibilityIdentifier("review.row.\(row.id)")
    }
}

/// The right pane: eyebrow, question, the comparison, the options, and "Search the
/// library for another…".
private struct QuestionDetail: View {
    let model: ReviewModel
    let row: ReviewModel.Row
    @Binding var searching: Bool
    var keyboardFocus: FocusState<Bool>.Binding
    let preview: ReviewPreview
    let togglePreview: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    Text(row.eyebrow.uppercased()).sectionLabelStyle()
                        .accessibilityIdentifier("review.eyebrow")
                    if row.preview != nil {
                        let playing = preview.rowID == row.id
                        Button(action: togglePreview) {
                            HStack(spacing: 4) {
                                Image(systemName: playing ? "stop.fill" : "play.fill").font(.system(size: 9))
                                Text(playing ? "Stop" : "Preview")
                                KeyCap("space")
                            }
                        }
                        .buttonStyle(SmallBorderedButtonStyle())
                        .accessibilityLabel(playing ? "Stop preview" : "Preview audio")
                        .accessibilityIdentifier("review.preview")
                    }
                }
                if let issue = row.issue {
                    let question = ReviewModel.question(for: issue)
                    Text(question ?? issue.message)
                        .font(Typography.question)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                        .accessibilityIdentifier("review.question")
                    if question != nil {
                        Text(issue.message)
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.text2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    comparison(issue)
                    options
                    if let type = model.searchType {
                        if searching {
                            LibraryPicker(type: type, model: model) {
                                searching = false
                                keyboardFocus.wrappedValue = true
                            }
                        } else {
                            Button {
                                searching = true
                            } label: {
                                HStack(spacing: 6) {
                                    Text("Search the library for another…")
                                    KeyCap("/")
                                }
                            }
                            .accessibilityIdentifier("review.search")
                        }
                    }
                } else {
                    Text(row.outcome ?? "").font(Typography.question)
                    comparisonTable(candidate: nil)
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 20)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("review.detail")
    }

    /// The candidate compared: the one picked, else the best.
    @ViewBuilder private func comparison(_ issue: Components.Schemas.Issue) -> some View {
        let pick = model.pick(for: row)
        let candidate =
            issue.candidates.first { $0.id == pick?.candidateID }
            ?? (pick?.choice == .pickCandidate ? nil : issue.candidates.first)
        if !row.incoming.isEmpty, candidate != nil || pick?.candidateID != nil {
            comparisonTable(
                candidate: candidate.map { ($0.title, $0.subtitle, $0.detail) }
                    ?? pick.map { ($0.title, nil, $0.detail) })
        }
    }

    private func comparisonTable(candidate: (title: String, subtitle: String?, detail: String?)?) -> some View {
        let library: [String: String] =
            candidate.map { candidate in
                var values = [String(localized: "Title"): candidate.title, String(localized: "Name"): candidate.title]
                if let subtitle = candidate.subtitle, !subtitle.isEmpty {
                    values[String(localized: "Artist")] = subtitle
                    values[String(localized: "Note")] = subtitle
                }
                if let detail = candidate.detail, !detail.isEmpty {
                    values[String(localized: "Length")] = detail
                    values[String(localized: "Details")] = detail
                }
                return values
            } ?? [:]
        return Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
            GridRow {
                Text("")
                Text("Incoming").font(Typography.name)
                if candidate != nil { Text("In library").font(Typography.name) }
            }
            .padding(.vertical, 6)
            .background(Palette.side)
            ForEach(Array(row.incoming.enumerated()), id: \.offset) { _, fact in
                Divider().gridCellUnsizedAxes(.horizontal)
                GridRow {
                    Text(fact.0).foregroundStyle(Palette.text2).frame(width: 110, alignment: .leading)
                    Text(fact.1).frame(maxWidth: .infinity, alignment: .leading)
                    if candidate != nil {
                        Text(library[fact.0] ?? "—").frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.vertical, 6)
            }
        }
        .font(Typography.body)
        .padding(.horizontal, 12)
        .overlay(RoundedRectangle(cornerRadius: Radius.card).strokeBorder(Palette.line, lineWidth: Metrics.hairline))
        .clipShape(RoundedRectangle(cornerRadius: Radius.card))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("review.comparison")
    }

    private var options: some View {
        let pick = model.pick(for: row)
        return VStack(alignment: .leading, spacing: 6) {
            ForEach(row.options) { option in
                OptionView(option: option, chosen: option.id == pick?.id) { model.choose(option, in: row) }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Decision")
        .accessibilityIdentifier("review.options")
    }
}

/// An option (padding 10 12, radius 8, 1 pt border; chosen: 2 pt `--sel` border on
/// `--selsoft`) with its key cap.
private struct OptionView: View {
    let option: ReviewModel.Option
    let chosen: Bool
    let choose: () -> Void

    var body: some View {
        Button(action: choose) {
            HStack(spacing: 8) {
                Image(systemName: chosen ? "largecircle.fill.circle" : "circle")
                    .foregroundStyle(chosen ? Palette.sel : Palette.text3)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 4) {
                        Text(option.title).font(chosen ? Typography.name : Typography.body)
                        if let score = option.score {
                            Text("· score \(ImportFormat.score(score))")
                                .font(Typography.body.monospacedDigit())
                                .foregroundStyle(Palette.text2)
                        }
                    }
                    if let detail = option.detail, !detail.isEmpty {
                        Text(detail).font(Typography.secondary).foregroundStyle(Palette.text2)
                    }
                }
                Spacer(minLength: 8)
                if let key = option.key { KeyCap(key) }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(chosen ? Palette.selSoft : .clear, in: RoundedRectangle(cornerRadius: Radius.largeButton))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.largeButton)
                    .strokeBorder(chosen ? Palette.sel : Palette.line, lineWidth: chosen ? 2 : 1)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            [option.title, option.score.map { "score \(ImportFormat.score($0))" }, option.detail]
                .compactMap { $0 }.joined(separator: ", ")
        )
        .accessibilityAddTraits(chosen ? [.isSelected] : [])
        .accessibilityHint(option.key.map { "Key \($0)" } ?? "")
        .accessibilityIdentifier("review.option.\(option.id)")
    }
}

/// `.kbd`: min width 16, padding 0 4, 1 pt border, radius 4, 11 pt.
private struct KeyCap: View {
    let key: String
    init(_ key: String) { self.key = key }

    var body: some View {
        Text(key)
            .font(Typography.meta)
            .foregroundStyle(Palette.text2)
            .padding(.horizontal, 4)
            .frame(minWidth: 16, minHeight: 16)
            .overlay(RoundedRectangle(cornerRadius: Radius.chip).strokeBorder(Palette.line, lineWidth: 1))
            .accessibilityHidden(true)
    }
}

/// "Search the library for another…" (/): a library search (`SearchStore`) for the row
/// type the issue may be answered with; the hit chosen becomes the pick.
private struct LibraryPicker: View {
    let type: Components.Schemas.EntityType
    let model: ReviewModel
    let close: () -> Void

    @Environment(ServerConnection.self) private var connection
    @State private var search: SearchStore?
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            TextField(
                "Search the library",
                text: Binding(get: { search?.query ?? "" }, set: { search?.update($0) })
            )
            .textFieldStyle(.roundedBorder)
            .focused($focused)
            .onKeyPress(.downArrow) {
                search?.move(1); return .handled
            }
            .onKeyPress(.upArrow) {
                search?.move(-1); return .handled
            }
            .onKeyPress(.escape) {
                close(); return .handled
            }
            .onSubmit { if let hit = search?.selected { choose(hit) } }
            .accessibilityIdentifier("review.search.field")
            if let search {
                ForEach(Array(search.hits.prefix(6).enumerated()), id: \.element.id) { index, hit in
                    Button {
                        choose(hit)
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(hit.title).font(Typography.name)
                            Text(hit.detail).font(Typography.secondary).foregroundStyle(Palette.text2)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(
                            index == search.selection ? Palette.selSoft : .clear,
                            in: RoundedRectangle(cornerRadius: Radius.row)
                        )
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityIdentifier("review.search.row.\(index)")
                }
            }
        }
        .onAppear {
            if search == nil { search = SearchStore(connection: connection, types: [type], grouped: false) }
            focused = true
        }
    }

    private func choose(_ hit: EntitySummary) {
        model.addCandidate(id: hit.ref.id, title: hit.title, detail: hit.detail)
        close()
    }
}
