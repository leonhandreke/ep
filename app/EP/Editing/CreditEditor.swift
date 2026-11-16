import EPAPI
import SwiftUI

/// An artist credit as tokens (board 9): a chip per artist, the joining text between
/// them as an editable dashed token (" feat. "), and "+ artist", which searches the
/// library's artists (or creates a new one). ⌥, in the artist field goes to the joining
/// text before it.
struct CreditEditor: View {
    @Binding var parts: [CreditPart]
    let id: String

    @Environment(ServerConnection.self) private var connection
    @State private var search: SearchStore?
    @State private var text = ""
    @FocusState private var focus: Field?

    private enum Field: Hashable {
        case add
        case join(UUID)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            FlowLayout(spacing: 4) {
                ForEach(Array(parts.enumerated()), id: \.element.id) { index, part in
                    chip(part, index: index)
                    if index < parts.count - 1 {
                        joinToken(index)
                    }
                }
                addField
            }
            .padding(4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.field, in: RoundedRectangle(cornerRadius: Radius.button))
            .overlay(
                RoundedRectangle(cornerRadius: Radius.button)
                    .strokeBorder(focus == nil ? Palette.line : Palette.sel, lineWidth: focus == nil ? 1 : 2)
            )
            if focus == .add, !text.trimmingCharacters(in: .whitespaces).isEmpty {
                suggestions
            }
            Text(help)
                .font(Typography.secondary)
                .foregroundStyle(Palette.text3)
                .fixedSize(horizontal: false, vertical: true)
        }
        .onAppear {
            if search == nil {
                search = SearchStore(connection: connection, types: [.artist], grouped: false)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Artist credit")
        .accessibilityIdentifier(id)
    }

    private var help: String {
        let reads = parts.isEmpty ? "" : String(localized: "Reads as “\(Credit.rendered(parts))”. ")
        return reads + String(localized: "Type to search artists; ⌥, inserts joining text.")
    }

    private func chip(_ part: CreditPart, index: Int) -> some View {
        HStack(spacing: 4) {
            Text(part.name).font(Typography.body)
            if part.artistID == nil {
                Text("new").font(Typography.meta).foregroundStyle(Palette.text3)
            }
            Button {
                parts.remove(at: index)
            } label: {
                Image(systemName: "xmark").font(.system(size: 8, weight: .semibold)).foregroundStyle(Palette.text3)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(part.name)")
            .accessibilityIdentifier("\(id).remove.\(index)")
        }
        .padding(.leading, 8)
        .padding(.trailing, 6)
        .frame(height: 22)
        .background(Palette.chip, in: Capsule())
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("\(id).artist.\(index)")
    }

    private func joinToken(_ index: Int) -> some View {
        TextField("", text: $parts[index].joinPhrase, prompt: Text(verbatim: "␣&␣"))
            .textFieldStyle(.plain)
            .font(.system(size: 12, design: .monospaced))
            .focused($focus, equals: .join(parts[index].id))
            .fixedSize()
            .frame(minWidth: 24)
            .padding(.horizontal, 4)
            .frame(height: 22)
            .overlay(
                RoundedRectangle(cornerRadius: Radius.chip)
                    .strokeBorder(Palette.text3, style: StrokeStyle(lineWidth: 1, dash: [3, 2]))
            )
            .accessibilityLabel("Joining text after \(parts[index].name)")
            .accessibilityIdentifier("\(id).join.\(index)")
    }

    private var addField: some View {
        TextField("", text: $text, prompt: Text("+ artist"))
            .textFieldStyle(.plain)
            .font(Typography.body)
            .focused($focus, equals: .add)
            .frame(width: 140, height: 22)
            .onChange(of: text) { _, new in search?.update(new) }
            .onKeyPress(.downArrow) {
                search?.move(1)
                return .handled
            }
            .onKeyPress(.upArrow) {
                search?.move(-1)
                return .handled
            }
            .onKeyPress(phases: .down) { press in
                // ⌥, (the key for "," with Option, whatever character it types).
                guard press.modifiers == .option, press.key == "," || press.characters == "≤",
                    let last = parts.last
                else { return .ignored }
                if parts.count > 0, last.joinPhrase.isEmpty { parts[parts.count - 1].joinPhrase = ", " }
                focus = .join(last.id)
                return .handled
            }
            .onSubmit(addSelected)
            .accessibilityLabel("Add artist")
            .accessibilityIdentifier("\(id).add")
    }

    /// The artists found, then "New artist “…”".
    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 0) {
            let hits = Array((search?.hits ?? []).prefix(5))
            ForEach(Array(hits.enumerated()), id: \.element.id) { index, hit in
                suggestionRow(hit.title, detail: hit.detail, selected: index == search?.selection) {
                    add(CreditPart(artistID: hit.ref.id, name: hit.title, joinPhrase: ""))
                }
                .accessibilityIdentifier("\(id).suggestion.\(index)")
            }
            let name = text.trimmingCharacters(in: .whitespaces)
            suggestionRow(String(localized: "New artist “\(name)”"), detail: nil, selected: hits.isEmpty) {
                add(CreditPart(newArtistName: name, name: name, joinPhrase: ""))
            }
            .accessibilityIdentifier("\(id).newArtist")
        }
        .padding(4)
        .background(Palette.bg, in: RoundedRectangle(cornerRadius: Radius.largeButton))
        .overlay(RoundedRectangle(cornerRadius: Radius.largeButton).strokeBorder(Palette.line))
    }

    private func suggestionRow(_ title: String, detail: String?, selected: Bool, action: @escaping () -> Void)
        -> some View
    {
        Button(action: action) {
            HStack {
                Text(title).font(Typography.body).lineLimit(1)
                if let detail, !detail.isEmpty {
                    Text(detail).font(Typography.meta).foregroundStyle(selected ? Palette.selText : Palette.text3)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .foregroundStyle(selected ? Palette.selText : Palette.text)
            .padding(.horizontal, 8)
            .frame(height: RowHeight.tagSuggestion)
            .background(selected ? Palette.sel : .clear, in: RoundedRectangle(cornerRadius: Radius.row))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func addSelected() {
        let name = text.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        if let hit = search?.selected, search?.hits.isEmpty == false {
            add(CreditPart(artistID: hit.ref.id, name: hit.title, joinPhrase: ""))
        } else {
            add(CreditPart(newArtistName: name, name: name, joinPhrase: ""))
        }
    }

    /// Appends an artist; two artists are joined by " & " until the user says otherwise.
    private func add(_ part: CreditPart) {
        if let last = parts.indices.last, parts[last].joinPhrase.isEmpty { parts[last].joinPhrase = " & " }
        parts.append(part)
        text = ""
        search?.update("")
        focus = .add
    }
}
