import EPAPI
import SwiftUI

/// The artists column (§3.2): filter field with the live count, the radio-only toggle,
/// and the artists by sort name, 22 pt rows: one row per artist in the list, loaded a
/// page at a time as rows appear. Type-to-select selects the first artist that sorts at
/// or after what was typed; the selected artist (also one shown by navigation) is
/// scrolled to, its page loaded first.
struct ArtistsColumn: View {
    var focus: FocusState<LibraryColumn?>.Binding
    let moveRight: () -> Void

    @Environment(ArtistList.self) private var list
    @Environment(LibraryBrowser.self) private var browser
    @Environment(ServerConnection.self) private var connection
    @FocusState private var filterFocused: Bool
    @State private var typed = ""
    @State private var typedAt = ContinuousClock.Instant.now
    @State private var typeSelection: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 0) {
            filterRow
            content
        }
    }

    // MARK: - Filter and radio-only toggle

    private var counts: Components.Schemas.LibraryCountsRead? {
        if case .connected(let server) = connection.state { server.counts } else { nil }
    }

    private var filterPlaceholder: String {
        guard let counts else { return String(localized: "Filter artists") }
        let total = counts.artists + (list.includeRadioOnly ? counts.radioOnlyArtists : 0)
        return String(localized: "Filter \(LibraryFormat.count(total)) artists")
    }

    private var filterRow: some View {
        HStack(spacing: 4) {
            HStack(spacing: 5) {
                Image(systemName: "line.3.horizontal.decrease")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(Palette.text3)
                    .accessibilityHidden(true)
                TextField(
                    "Filter artists",
                    text: Binding(get: { list.filter }, set: { list.setFilter($0) }),
                    prompt: Text(filterPlaceholder)
                )
                .textFieldStyle(.plain)
                .font(Typography.secondary)
                .labelsHidden()
                .focused($filterFocused)
                .onExitCommand { list.setFilter("") }
                .onKeyPress(.downArrow) {
                    focus.wrappedValue = .artists
                    if browser.location.artistID == nil, let first = list.artist(at: 0) {
                        browser.select(artist: first.id)
                    }
                    return .handled
                }
                .accessibilityLabel("Filter artists")
                .accessibilityIdentifier("artists.filter")
            }
            .padding(.horizontal, 7)
            .frame(height: Metrics.filterFieldHeight)
            .background(Palette.chip, in: RoundedRectangle(cornerRadius: Radius.filterField))

            radioOnlyToggle
        }
        .padding(.top, 8)
        .padding(.horizontal, 10)
        .padding(.bottom, 6)
    }

    private var radioOnlyToggle: some View {
        let on = list.includeRadioOnly
        let hidden = counts.map { LibraryFormat.count($0.radioOnlyArtists) }
        let label =
            on
            ? String(localized: "Hide artists known only from radio")
            : hidden.map { String(localized: "Show artists known only from radio (\($0) hidden)") }
                ?? String(localized: "Show artists known only from radio")
        return Button {
            list.setIncludeRadioOnly(!on)
        } label: {
            Image(systemName: "dot.radiowaves.left.and.right")
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(on ? Palette.accent : Palette.text3)
                .frame(width: Metrics.radioOnlyButton.width, height: Metrics.radioOnlyButton.height)
                .background(on ? Palette.selSoft : .clear, in: RoundedRectangle(cornerRadius: Radius.filterField))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(on ? "Hide radio-only artists" : "Show radio-only artists")
        .accessibilityLabel(label)
        .accessibilityAddTraits(on ? .isSelected : [])
        .accessibilityIdentifier("artists.radioOnly")
    }

    // MARK: - List

    @ViewBuilder private var content: some View {
        if list.total == 0 {
            switch list.phase {
            case .loading: DelayedSkeleton(rowHeight: RowHeight.artist)
            case .failed(let message): ColumnMessage(id: "artists", text: message) { list.reload() }
            case .loaded:
                ColumnMessage(
                    id: "artists",
                    text: list.query.map { String(localized: "No artists match “\($0)”.") }
                        ?? String(localized: "No artists yet."))
            case .idle: ConnectionStatusText()
            }
        } else {
            artistList
        }
    }

    private var artistList: some View {
        ScrollViewReader { proxy in
            List(selection: Binding(get: { browser.location.artistID }, set: { browser.select(artist: $0) })) {
                ForEach(0..<list.total, id: \.self) { index in
                    Group {
                        if let artist = list.artist(at: index) {
                            ArtistRow(
                                artist: artist,
                                emphasized: focus.wrappedValue == .artists && artist.id == browser.location.artistID
                            )
                            .tag(artist.id)
                        } else {
                            PlaceholderRow(index: index)
                                .onAppear { list.loadPage(containing: index) }
                        }
                    }
                    .listRowInsets(EdgeInsets(top: 0, leading: Metrics.rowPadding, bottom: 0, trailing: 0))
                    .listRowSeparator(.hidden)
                }
            }
            .listStyle(.inset)
            .scrollContentBackground(.hidden)
            .environment(\.defaultMinListRowHeight, RowHeight.artist)
            .focused(focus, equals: .artists)
            .onKeyPress(.rightArrow) {
                moveRight()
                return .handled
            }
            .onKeyPress(phases: .down) { press in typeSelect(press) }
            .contextMenu(forSelectionType: String.self) { ids in
                if let id = ids.first, let artist = list.index(of: id).flatMap(list.artist(at:)) {
                    Button("Copy Link") { copyNoteLink(artist.link) }
                }
            }
            .task(id: RevealKey(artistID: browser.location.artistID, list: list.listVersion)) {
                guard let id = browser.location.artistID, let index = await list.reveal(id),
                    !Task.isCancelled
                else { return }
                proxy.scrollTo(index)
            }
            .accessibilityIdentifier("artists.list")
        }
    }

    /// Letters typed into the focused list select the first matching artist; letters typed
    /// within `typeSelectTimeout` of each other add up.
    private func typeSelect(_ press: KeyPress) -> KeyPress.Result {
        guard press.modifiers.isDisjoint(with: [.command, .control, .option]),
            let scalar = press.characters.unicodeScalars.first,
            CharacterSet.alphanumerics.contains(scalar) || press.characters == " "
        else { return .ignored }
        let now = ContinuousClock.Instant.now
        // A space only continues what is being typed; on its own it is not a search.
        if press.characters == " ", now - typedAt >= Metrics.typeSelectTimeout { return .ignored }
        PlaybackKeys.noteTypeSelect()
        typed = now - typedAt < Metrics.typeSelectTimeout ? typed + press.characters : press.characters
        typedAt = now
        typeSelection?.cancel()
        typeSelection = Task { [typed] in
            guard let id = await list.artistID(atOrAfter: typed), !Task.isCancelled else { return }
            browser.select(artist: id)
        }
        return .handled
    }
}

/// Scroll to the selection when it changes, and when a new list (filter) arrives.
private struct RevealKey: Equatable {
    let artistID: String?
    let list: Int
}

/// A row whose page is not loaded yet: a skeleton bar of the row's height.
private struct PlaceholderRow: View {
    let index: Int

    var body: some View {
        RoundedRectangle(cornerRadius: 3)
            .fill(Palette.skel)
            .frame(width: [90, 120, 70, 105][index % 4], height: 8)
            .frame(maxWidth: .infinity, minHeight: RowHeight.artist, maxHeight: RowHeight.artist, alignment: .leading)
            .accessibilityHidden(true)
    }
}

private struct ArtistRow: View {
    let artist: Components.Schemas.ArtistRead
    let emphasized: Bool

    var body: some View {
        let colors = RowForeground(emphasized: emphasized)
        HStack(spacing: 5) {
            Text(artist.name)
                .font(Typography.body)
                .foregroundStyle(colors.primary)
            // Two artists of one name (two "Arca"s) are told apart by their disambiguation.
            if !artist.disambiguation.isEmpty {
                Text(artist.disambiguation)
                    .font(Typography.meta)
                    .foregroundStyle(colors.tertiary)
            }
        }
        .lineLimit(1)
        .truncationMode(.tail)
        .frame(maxWidth: .infinity, minHeight: RowHeight.artist, maxHeight: RowHeight.artist, alignment: .leading)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("artists.row.\(artist.id)")
    }
}

/// What the connection is doing, in the artists column until it has artists to show.
struct ConnectionStatusText: View {
    @Environment(ServerConnection.self) private var connection

    var body: some View {
        Group {
            switch connection.state {
            case .connecting:
                Text("Connecting to \(connection.serverURL.hostAndPort)…")
            case .connected(let server):
                Text("Connected to \(connection.serverURL.hostAndPort) · EP \(server.version)")
            case .unreachable:
                Text("Not connected")
            }
        }
        .font(Typography.secondary)
        .foregroundStyle(Palette.text3)
        .multilineTextAlignment(.center)
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityIdentifier("connection.status")
    }
}
