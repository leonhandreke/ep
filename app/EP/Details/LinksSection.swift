import EPAPI
import SwiftUI

/// LINKS in the artist and album panels: the entity's pages elsewhere (Wikipedia, its
/// homepage, Bandcamp, Discogs, … and MusicBrainz last), from `GET /artists/{id}/links`
/// or `GET /release-groups/{id}/links`, in the server's order. The server asks
/// MusicBrainz (cached for a day), so a first load can take a moment: "Loading links…"
/// shows after 300 ms. Left out when there are none (no MBID); "Couldn't reach
/// MusicBrainz" with Retry on a 502. A click opens the page in the browser.
struct LinksSection: View {
    let ref: EntityRef
    let id: String

    @Environment(ServerConnection.self) private var connection
    @State private var phase = Phase.loading
    /// Bumped by Retry: loads again.
    @State private var attempt = 0
    @State private var showsLoading = false

    enum Phase: Equatable {
        case loading
        case loaded([Components.Schemas.ServiceLinkRead])
        /// MusicBrainz did not answer (502).
        case unreachable
        case failed
    }

    var body: some View {
        Group {
            switch phase {
            case .loaded(let links) where links.isEmpty:
                EmptyView()
            case .loaded(let links):
                section {
                    FlowLayout(spacing: 14) {
                        ForEach(Array(links.enumerated()), id: \.offset) { index, link in
                            LinkItem(link: link, id: "\(id).link.\(index)")
                        }
                    }
                }
            case .loading:
                if showsLoading {
                    section {
                        TextRow(spacing: 6) {
                            ProgressView().controlSize(.mini).centeredOnText(size: 12)
                            Text("Loading links…").font(Typography.secondary).foregroundStyle(Palette.text3)
                        }
                    }
                }
            case .unreachable, .failed:
                section {
                    TextRow(spacing: 8) {
                        Text(
                            phase == .unreachable
                                ? String(localized: "Couldn’t reach MusicBrainz.")
                                : String(localized: "Couldn’t load the links.")
                        )
                        .font(Typography.secondary)
                        .foregroundStyle(Palette.text3)
                        Button("Retry") { attempt += 1 }
                            .buttonStyle(.link)
                            .font(Typography.secondary)
                            .accessibilityIdentifier("\(id).links.retry")
                    }
                }
            }
        }
        .task(id: LoadKey(ref: ref, attempt: attempt)) { await load() }
    }

    private func section(@ViewBuilder _ content: () -> some View) -> some View {
        DetailSection(title: String(localized: "Links"), id: "\(id).links", content: content)
    }

    private struct LoadKey: Equatable {
        let ref: EntityRef
        let attempt: Int
    }

    private func load() async {
        phase = .loading
        showsLoading = false
        let reveal = Task {
            try? await Task.sleep(for: Metrics.skeletonDelay)
            if !Task.isCancelled { showsLoading = true }
        }
        defer { reveal.cancel() }
        do {
            let next = try await fetch()
            guard !Task.isCancelled else { return }
            phase = next
        } catch {
            guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
            phase = .failed
            connection.reportFailure(error)
        }
    }

    private func fetch() async throws -> Phase {
        let client = connection.client
        switch ref.type {
        case .artist:
            switch try await client.listArtistLinks(path: .init(id: ref.id)) {
            case .ok(let ok): return .loaded(try ok.body.json)
            case .notFound: return .loaded([])
            case .badGateway: return .unreachable
            default: return .failed
            }
        case .releaseGroup:
            switch try await client.listReleaseGroupLinks(path: .init(id: ref.id)) {
            case .ok(let ok): return .loaded(try ok.body.json)
            case .notFound: return .loaded([])
            case .badGateway: return .unreachable
            default: return .failed
            }
        default:
            return .loaded([])
        }
    }
}

/// One link: a neutral symbol and the service's name, in `--accent`; the URL as tooltip.
private struct LinkItem: View {
    let link: Components.Schemas.ServiceLinkRead
    let id: String

    @Environment(\.openURL) private var openURL

    var body: some View {
        Button {
            if let url = URL(string: link.url) { openURL(url) }
        } label: {
            TextRow(spacing: 5) {
                Image(systemName: link.service.symbol)
                    .font(.system(size: 11))
                    .frame(width: 14)
                    .centeredOnText()
                    .accessibilityHidden(true)
                Text(link.title).font(Typography.body)
            }
            .foregroundStyle(Palette.accent)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .pointerStyle(.link)
        .help(link.url)
        .accessibilityLabel(link.title)
        .accessibilityHint("Opens \(link.url) in the browser")
        .accessibilityIdentifier(id)
    }
}

extension Components.Schemas.ServiceLinkRead {
    /// "Wikipedia", "Wikipedia (de)" for another language than English, "Bandcamp".
    var title: String {
        let name = service.displayName
        guard service == .wikipedia, let label, !label.isEmpty, label.lowercased() != "en" else { return name }
        return "\(name) (\(label))"
    }
}

extension Components.Schemas.LinkService {
    var displayName: String {
        switch self {
        case .wikipedia: "Wikipedia"
        case .officialHomepage: String(localized: "Homepage")
        case .bandcamp: "Bandcamp"
        case .spotify: "Spotify"
        case .appleMusic: "Apple Music"
        case .youtube: "YouTube"
        case .discogs: "Discogs"
        case .allmusic: "AllMusic"
        case .musicbrainz: "MusicBrainz"
        }
    }

    /// A neutral SF Symbol per kind of page (no brand logos).
    var symbol: String {
        switch self {
        case .wikipedia: "book.closed"
        case .officialHomepage: "house"
        case .bandcamp, .spotify, .appleMusic: "music.note"
        case .youtube: "play.rectangle"
        case .discogs: "opticaldisc"
        case .allmusic: "music.quarternote.3"
        case .musicbrainz: "link"
        }
    }
}
