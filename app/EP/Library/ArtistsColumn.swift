import EPAPI
import SwiftUI

/// The artists column (§3.2): filter field with the live count, the radio-only toggle,
/// and the artists by sort name, 22 pt rows. Type-to-select jumps to the first loaded
/// artist whose sort name or name starts with what was typed.
struct ArtistsColumn: View {
    var focus: FocusState<LibraryColumn?>.Binding
    let moveRight: () -> Void

    @Environment(ArtistList.self) private var list
    @Environment(LibraryBrowser.self) private var browser
    @Environment(ServerConnection.self) private var connection
    @FocusState private var filterFocused: Bool
    @State private var typed = ""
    @State private var typedAt = ContinuousClock.Instant.now

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
                    if browser.location.artistID == nil, let first = list.artists.first {
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
        if list.artists.isEmpty {
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
                ForEach(list.artists, id: \.id) { artist in
                    ArtistRow(
                        artist: artist,
                        emphasized: focus.wrappedValue == .artists && artist.id == browser.location.artistID
                    )
                    .listRowInsets(EdgeInsets(top: 0, leading: Metrics.rowPadding, bottom: 0, trailing: 0))
                    .listRowSeparator(.hidden)
                    .onAppear { list.loadMoreIfNeeded(after: artist.id) }
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
                if let id = ids.first, let artist = list.artists.first(where: { $0.id == id }) {
                    Button("Copy Link") { copyNoteLink(artist.link) }
                }
            }
            .onChange(of: browser.location.artistID) { _, id in
                if let id { proxy.scrollTo(id) }
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
        typed = now - typedAt < Metrics.typeSelectTimeout ? typed + press.characters : press.characters
        typedAt = now
        if let artist = list.firstArtist(matching: typed) {
            browser.select(artist: artist.id)
        }
        return .handled
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
