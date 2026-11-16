import EPAPI
import SwiftUI

/// The body of a detail panel (§3.7): Note, Tags and Linked from stacked, 22 pt apart,
/// scrolling together. `id` prefixes the identifiers (`detail.artist`, …). The Radio view
/// (stage 7) hosts the same three sections in tabs.
struct DetailPanelBody: View {
    let details: EntityDetails
    let id: String
    let kind: DetailKind

    var body: some View {
        switch details.phase {
        case .idle:
            Color.clear
        case .loading where details.entity == nil:
            NoteSkeleton()
        case .failed(let message) where details.entity == nil:
            ColumnMessage(id: id, text: message) { details.reload() }
        default:
            ScrollView {
                VStack(alignment: .leading, spacing: Metrics.sectionSpacing) {
                    NoteSection(details: details, id: id)
                    TagsSection(details: details, id: id, hint: kind.tagsHint)
                    LinkedFromSection(details: details, id: id, kind: kind)
                }
                .padding(.top, 14)
                .padding(.horizontal, 18)
                .padding(.bottom, 24)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// Which panel the sections are in: the hints and headings differ (§3.7).
enum DetailKind {
    case artist(name: String?), album, track, show, episode

    var tagsHint: LocalizedStringKey? {
        switch self {
        case .artist: "Click a tag to list every artist, album and track with it."
        case .album: "Return adds the highlighted tag. Tags on an album do not copy to its tracks."
        case .track: "Tags on a track belong to its recording, so they also show on the radio excerpt."
        case .show, .episode: nil
        }
    }

    func radioHeading(_ count: Int) -> String {
        switch self {
        case .artist(let name?): String(localized: "Radio episodes that played \(name) · \(count)")
        case .album: String(localized: "Radio episodes that played this album · \(count)")
        default: String(localized: "Radio episodes that played this · \(count)")
        }
    }
}

/// A section: 11 pt uppercase label over a hairline, with an optional trailing accessory.
struct DetailSection<Accessory: View, Content: View>: View {
    let title: String
    let id: String
    @ViewBuilder var accessory: Accessory
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).sectionLabelStyle().accessibilityAddTraits(.isHeader)
                Spacer(minLength: 8)
                accessory
            }
            .padding(.bottom, 6)
            .overlay(alignment: .bottom) { HairlineDivider() }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(id)
    }
}

extension DetailSection where Accessory == EmptyView {
    init(title: String, id: String, @ViewBuilder content: () -> Content) {
        self.init(title: title, id: id, accessory: { EmptyView() }, content: content)
    }
}

/// The note skeleton (§11.3): a 12 pt bar at 40 % and 9 pt bars at 92 % and 78 %, shown
/// only after 300 ms.
private struct NoteSkeleton: View {
    @State private var visible = false

    var body: some View {
        GeometryReader { geometry in
            VStack(alignment: .leading, spacing: 10) {
                if visible {
                    bar(width: geometry.size.width * 0.4, height: 12)
                    bar(width: geometry.size.width * 0.92, height: 9)
                    bar(width: geometry.size.width * 0.78, height: 9)
                }
            }
        }
        .padding(.top, 18)
        .padding(.horizontal, 18)
        .task {
            try? await Task.sleep(for: Metrics.skeletonDelay)
            visible = true
        }
        .accessibilityElement()
        .accessibilityLabel("Loading")
        .accessibilityIdentifier("skeleton")
    }

    private func bar(width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 3).fill(Palette.skel).frame(width: width, height: height)
    }
}

// MARK: - Tags

/// TAGS · n: chips (#name, count, remove), the "+ Add tag" field with suggestions, and a
/// per-kind hint.
struct TagsSection: View {
    let details: EntityDetails
    let id: String
    let hint: LocalizedStringKey?

    @Environment(\.showTag) private var showTag

    var body: some View {
        DetailSection(title: String(localized: "Tags · \(details.tags.count)"), id: "\(id).tags") {
            FlowLayout(spacing: 6) {
                ForEach(details.tags, id: \.id) { tag in
                    TagChip(tag: tag, id: "\(id).tag.\(tag.id)", show: showTag) { details.removeTag(tag) }
                }
                AddTagField(details: details, id: "\(id).addTag")
            }
            if let error = details.tagError {
                Text(error)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.danger)
            }
            if let hint {
                Text(hint)
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct TagChip: View {
    let tag: Components.Schemas.TagRead
    let id: String
    let show: ((Components.Schemas.TagRead) -> Void)?
    let remove: () -> Void

    var body: some View {
        HStack(spacing: 4) {
            Button {
                show?(tag)
            } label: {
                Text(verbatim: "#\(tag.name)")
                    .font(Typography.body)
                    .foregroundStyle(Palette.text)
            }
            .buttonStyle(.plain)
            .pointerStyle(.link)
            .help("Show everything tagged \(tag.name)")
            .accessibilityLabel("Tag \(tag.name), \(tag.count) items")
            .accessibilityHint("Shows everything tagged \(tag.name)")
            .accessibilityIdentifier(id)
            Text(LibraryFormat.count(tag.count))
                .font(Typography.meta)
                .foregroundStyle(Palette.text3)
                .monospacedDigit()
                .accessibilityHidden(true)
            Button(action: remove) {
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(Palette.text3)
                    .frame(width: 18, height: 18)
                    .contentShape(Circle())
            }
            .buttonStyle(RemoveTagButtonStyle())
            .accessibilityLabel("Remove tag \(tag.name)")
            .accessibilityIdentifier("\(id).remove")
        }
        .padding(.leading, 10)
        .padding(.trailing, 4)
        .frame(height: RowHeight.tagChip)
        .background(Palette.chip, in: Capsule())
    }
}

private struct RemoveTagButtonStyle: ButtonStyle {
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(Circle().fill(hovering || configuration.isPressed ? Palette.line : .clear))
            .onHover { hovering = $0 }
    }
}

/// The dashed "+ Add tag" pill; typing shows suggestions below it.
private struct AddTagField: View {
    let details: EntityDetails
    let id: String

    @Environment(ServerConnection.self) private var connection
    @Environment(CoverImages.self) private var covers
    @State private var text = ""
    @State private var suggestions: TagSuggestions?
    @State private var popup = FloatingPopup()
    @State private var anchor: NSView?
    @FocusState private var focused: Bool

    var body: some View {
        TextField("", text: $text, prompt: Text("+ Add tag").foregroundStyle(Palette.text3))
            .textFieldStyle(.plain)
            .font(Typography.body)
            .focused($focused)
            .frame(width: text.isEmpty ? 70 : 140)
            .padding(.horizontal, 10)
            .frame(height: RowHeight.tagChip)
            .overlay(
                Capsule().strokeBorder(Palette.text3, style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
            )
            .background(PopupAnchor { anchor = $0 })
            .onSubmit(add)
            .onKeyPress(.downArrow) {
                suggestions?.move(1)
                return .handled
            }
            .onKeyPress(.upArrow) {
                suggestions?.move(-1)
                return .handled
            }
            .onKeyPress(.escape) {
                text = ""
                return .handled
            }
            .onChange(of: text) { _, new in
                let model = suggestions ?? TagSuggestions(connection: connection)
                suggestions = model
                model.update(new, existing: details.tags)
            }
            .onChange(of: focused) { _, isFocused in
                if !isFocused { text = "" }
            }
            .onChange(of: popupState) { _, _ in updatePopup() }
            .onDisappear { popup.hide() }
            .accessibilityLabel("Add tag")
            .accessibilityIdentifier(id)
    }

    /// What the popup depends on, to show, move or hide it.
    private var popupState: [String] {
        guard focused, let suggestions, suggestions.isOpen else { return [] }
        return suggestions.rows.map(\.name) + ["\(suggestions.selection)"]
    }

    private func updatePopup() {
        guard focused, let suggestions, suggestions.isOpen, let anchor, let window = anchor.window,
            let frame = anchor.screenFrame
        else { return popup.hide() }
        let size = CGSize(width: 240, height: CGFloat(suggestions.rows.count) * RowHeight.tagSuggestion + 30)
        popup.show(
            TagSuggestionList(model: suggestions, id: "\(id).suggestions") { name in add(name) }
                .environment(connection).environment(covers),
            size: size, below: frame, in: window)
    }

    private func add() {
        guard let name = suggestions?.chosenName else { return }
        add(name)
    }

    private func add(_ name: String) {
        details.addTag(name)
        text = ""
        suggestions?.clear()
        popup.hide()
    }
}

/// The suggestion popover (width 240, radius 8, padding 4): "Typing “str”", existing
/// tags with counts, then "Create “#str”".
private struct TagSuggestionList: View {
    let model: TagSuggestions
    let id: String
    let choose: (String) -> Void

    var body: some View {
        PopupCard(radius: Radius.largeButton) {
            VStack(alignment: .leading, spacing: 0) {
                Text("Typing “\(model.trimmed)”")
                    .font(Typography.meta)
                    .foregroundStyle(Palette.text3)
                    .padding(.horizontal, 8)
                    .frame(height: 22, alignment: .leading)
                ForEach(Array(model.rows.enumerated()), id: \.offset) { index, row in
                    let selected = index == model.selection
                    HStack {
                        switch row {
                        case .existing(let tag):
                            Text(verbatim: "#\(tag.name)").lineLimit(1)
                            Spacer(minLength: 8)
                            Text(LibraryFormat.count(tag.count))
                                .font(Typography.meta)
                                .monospacedDigit()
                                .foregroundStyle(selected ? Palette.selText : Palette.text3)
                        case .create(let name):
                            Text("Create “#\(name)”")
                                .lineLimit(1)
                                .foregroundStyle(selected ? Palette.selText : Palette.text2)
                            Spacer(minLength: 0)
                        }
                    }
                    .font(Typography.body)
                    .foregroundStyle(selected ? Palette.selText : Palette.text)
                    .padding(.horizontal, 8)
                    .frame(height: RowHeight.tagSuggestion)
                    .background(selected ? Palette.sel : .clear, in: RoundedRectangle(cornerRadius: Radius.row))
                    .contentShape(Rectangle())
                    .onTapGesture { choose(row.name) }
                    .accessibilityElement(children: .combine)
                    .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
                    .accessibilityIdentifier("\(id).row.\(index)")
                }
            }
            .padding(4)
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Tag suggestions")
        .accessibilityIdentifier(id)
    }
}

// MARK: - Linked from

/// LINKED FROM: the notes that link here (with the line around each link) and the radio
/// episodes that played this, with play-from-here.
struct LinkedFromSection: View {
    let details: EntityDetails
    let id: String
    let kind: DetailKind

    var body: some View {
        DetailSection(title: String(localized: "Linked from"), id: "\(id).linkedFrom") {
            if details.backlinks.isEmpty && details.radioAppearances.isEmpty {
                Text("Nothing links here yet.")
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
            }
            if !details.backlinks.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    subheading(String(localized: "Notes that link here · \(details.backlinks.count)"))
                    ForEach(details.backlinks) { backlink in
                        BacklinkRow(backlink: backlink, id: "\(id).backlink.\(backlink.source.id)")
                    }
                }
            }
            if !details.radioAppearances.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    subheading(kind.radioHeading(details.radioAppearances.count))
                    ForEach(details.radioAppearances) { appearance in
                        RadioAppearanceRow(
                            appearance: appearance, details: details, id: "\(id).radio.\(appearance.id)")
                    }
                }
            }
        }
    }

    private func subheading(_ text: String) -> some View {
        Text(text)
            .font(Typography.subheading)
            .foregroundStyle(Palette.text2)
            .padding(.vertical, 4)
            .accessibilityAddTraits(.isHeader)
    }
}

private struct BacklinkRow: View {
    let backlink: Backlink
    let id: String

    @Environment(\.openEntity) private var openEntity

    var body: some View {
        Button {
            openEntity?(OpenTarget(entity: backlink.source), newWindow: NSEvent.modifierFlags.contains([.command, .option]))
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(backlink.kind)
                        .font(Typography.badge)
                        .tracking(0.4)
                        .textCase(.uppercase)
                        .foregroundStyle(Palette.text3)
                    Text(backlink.title)
                        .font(Typography.name)
                        .foregroundStyle(Palette.text)
                        .lineLimit(1)
                }
                ForEach(Array(backlink.snippets.enumerated()), id: \.offset) { _, snippet in
                    Text(Self.attributed(snippet))
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text2)
                        .lineLimit(1)
                        .truncationMode(.tail)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(HoverRowStyle())
        .accessibilityLabel("\(backlink.kind) \(backlink.title): \(backlink.snippets.first?.text ?? "")")
        .accessibilityHint("Opens \(backlink.title)")
        .accessibilityIdentifier(id)
    }

    /// The snippet with its link text in `--accent`.
    static func attributed(_ snippet: Backlink.Snippet) -> AttributedString {
        var attributed = AttributedString(snippet.text)
        for range in snippet.highlights {
            // UTF-16 offsets from the API, as NSRange.
            guard let range = Range(range, in: attributed) else { continue }
            attributed[range].foregroundColor = Palette.accent
        }
        return attributed
    }
}

private struct RadioAppearanceRow: View {
    let appearance: RadioAppearance
    let details: EntityDetails
    let id: String

    @Environment(\.openEntity) private var openEntity
    @Environment(Player.self) private var player

    var body: some View {
        HStack(spacing: 8) {
            Button(action: play) {
                Image(systemName: "play.fill")
                    .font(.system(size: 9))
                    .foregroundStyle(Palette.text2)
                    .frame(width: 24, height: 24)
                    .overlay(Circle().strokeBorder(Palette.line))
                    .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(!appearance.canPlay)
            .help("Play the episode from here")
            .accessibilityLabel("Play episode from \(appearance.position ?? String(localized: "the start"))")
            .accessibilityIdentifier("\(id).play")
            .frame(width: 26, alignment: .leading)

            Button(action: open) {
                HStack(alignment: .center, spacing: 8) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(appearance.heading)
                            .font(Typography.body)
                            .foregroundStyle(Palette.text)
                            .lineLimit(1)
                        Text(appearance.played)
                            .font(Typography.secondary)
                            .foregroundStyle(Palette.text3)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 8)
                    if let position = appearance.position {
                        Text(position)
                            .font(Typography.secondary)
                            .italic(appearance.isEstimated)
                            .monospacedDigit()
                            .foregroundStyle(Palette.text2)
                    }
                }
                .frame(minHeight: RowHeight.linkedFromRadioMin)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                "\(appearance.heading), \(appearance.played)\(appearance.position.map { ", at \($0)" } ?? "")")
            .accessibilityHint("Shows the episode")
            .accessibilityIdentifier(id)
        }
        .padding(.horizontal, 8)
        .accessibilityElement(children: .contain)
    }

    private func open() {
        openEntity?(
            OpenTarget(entity: EntityRef(type: .radioEpisode, id: appearance.episodeID), radioEntryID: appearance.id),
            newWindow: NSEvent.modifierFlags.contains([.command, .option]))
    }

    private func play() {
        Task {
            guard let episode = await details.episode(of: appearance) else { return }
            player.playEpisode(episode, from: appearance.id)
        }
    }
}

/// A row that shows a soft background while hovered or pressed.
struct HoverRowStyle: ButtonStyle {
    @State private var hovering = false

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(
                RoundedRectangle(cornerRadius: Radius.rowLarge)
                    .fill(configuration.isPressed ? Palette.selSoft : hovering ? Palette.chip.opacity(0.6) : .clear)
            )
            .onHover { hovering = $0 }
    }
}

/// Lays out subviews in rows, wrapping to the next row when one is full (tag chips).
struct FlowLayout: Layout {
    var spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = rows(width: proposal.width ?? .infinity, subviews: subviews)
        let width = rows.map(\.width).max() ?? 0
        let height = rows.map(\.height).reduce(0, +) + spacing * CGFloat(max(rows.count - 1, 0))
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(
                    at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + spacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func rows(width: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var current = Row()
        for index in subviews.indices {
            let size = subviews[index].sizeThatFits(.unspecified)
            if !current.indices.isEmpty, current.width + spacing + size.width > width {
                rows.append(current)
                current = Row()
            }
            current.width += (current.indices.isEmpty ? 0 : spacing) + size.width
            current.height = max(current.height, size.height)
            current.indices.append(index)
        }
        if !current.indices.isEmpty { rows.append(current) }
        return rows
    }
}
