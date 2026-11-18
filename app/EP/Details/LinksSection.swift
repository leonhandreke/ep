import EPAPI
import Observation
import SwiftUI

// The service logos (Assets.xcassets: ServiceWikipedia, ServiceBandcamp, ServiceSpotify,
// ServiceMusicbrainz) are Simple Icons 13.21.0 (https://simpleicons.org,
// https://cdn.jsdelivr.net/npm/simple-icons@13.21.0/icons/<name>.svg), CC0 1.0; the
// brands themselves are their owners' trademarks. Template images: drawn in the link's
// colour.

/// The links of the artist or album a panel shows (`GET /artists/{id}/links`,
/// `GET /release-groups/{id}/links`), owned by the panel, so the panel adds the LINKS
/// section only when there is something to show (no gap for an entity without links).
/// The server asks MusicBrainz (cached for a day), so a first load can take a moment:
/// `showsLoading` turns on after 300 ms.
@Observable
final class ServiceLinks {
    enum Phase: Equatable {
        case idle, loading
        case loaded([Components.Schemas.ServiceLinkRead])
        /// MusicBrainz did not answer (502).
        case unreachable
        case failed
    }

    private(set) var ref: EntityRef?
    private(set) var phase = Phase.idle
    private(set) var showsLoading = false

    @ObservationIgnored private let connection: ServerConnection
    @ObservationIgnored private var task: Task<Void, Never>?

    init(connection: ServerConnection) {
        self.connection = connection
    }

    /// Whether the panel shows the section: links, a failure, or a load that takes long.
    var isShown: Bool {
        switch phase {
        case .idle: false
        case .loading: showsLoading
        case .loaded(let links): !links.isEmpty
        case .unreachable, .failed: true
        }
    }

    /// Loads the links of `ref` (an artist or album; anything else has none).
    func show(_ ref: EntityRef?) {
        guard ref != self.ref else { return }
        self.ref = ref
        load()
    }

    func retry() { load() }

    /// Waits for the load in flight (tests).
    func settle() async { await task?.value }

    private func load() {
        task?.cancel()
        showsLoading = false
        guard let ref, ref.type == .artist || ref.type == .releaseGroup else {
            phase = .idle
            return
        }
        phase = .loading
        let client = connection.client
        task = Task {
            let reveal = Task {
                try? await Task.sleep(for: Metrics.skeletonDelay)
                if !Task.isCancelled, phase == .loading { showsLoading = true }
            }
            defer { reveal.cancel() }
            do {
                let next = try await Self.fetch(ref, client: client)
                guard !Task.isCancelled else { return }
                phase = next
            } catch {
                guard !Task.isCancelled, !ServerConnection.isCancellation(error) else { return }
                phase = .failed
                connection.reportFailure(error)
            }
        }
    }

    private static func fetch(_ ref: EntityRef, client: Client) async throws -> Phase {
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
            return .idle
        }
    }
}

/// LINKS in the artist and album panels: the entity's pages elsewhere (Wikipedia,
/// Bandcamp, Spotify, MusicBrainz), in the server's order, each its logo and name; a
/// click opens the page in the browser. "Loading links…" while a load takes long;
/// "Couldn't reach MusicBrainz" with Retry on a 502. Added only when `links.isShown`.
struct LinksSection: View {
    let links: ServiceLinks
    let id: String

    var body: some View {
        DetailSection(title: String(localized: "Links"), id: "\(id).links") {
            switch links.phase {
            case .loaded(let items):
                FlowLayout(spacing: 14) {
                    ForEach(Array(items.enumerated()), id: \.offset) { index, link in
                        LinkItem(link: link, id: "\(id).link.\(index)")
                    }
                }
            case .loading, .idle:
                TextRow(spacing: 6) {
                    ProgressView().controlSize(.mini).centeredOnText(size: 12)
                    Text("Loading links…").font(Typography.secondary).foregroundStyle(Palette.text3)
                }
            case .unreachable, .failed:
                TextRow(spacing: 8) {
                    Text(
                        links.phase == .unreachable
                            ? String(localized: "Couldn’t reach MusicBrainz.")
                            : String(localized: "Couldn’t load the links.")
                    )
                    .font(Typography.secondary)
                    .foregroundStyle(Palette.text3)
                    Button("Retry") { links.retry() }
                        .buttonStyle(.link)
                        .font(Typography.secondary)
                        .accessibilityIdentifier("\(id).links.retry")
                }
            }
        }
    }
}

/// One link: the service's logo and name in `--accent`; the URL as tooltip.
private struct LinkItem: View {
    let link: Components.Schemas.ServiceLinkRead
    let id: String

    @Environment(\.openURL) private var openURL

    /// The logos at about the size of the 13 pt text.
    private static let logoSize: CGFloat = 12

    var body: some View {
        Button {
            if let url = URL(string: link.url) { openURL(url) }
        } label: {
            TextRow(spacing: 5) {
                Image(link.service.logo)
                    .renderingMode(.template)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .frame(width: Self.logoSize, height: Self.logoSize)
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
        case .bandcamp: "Bandcamp"
        case .spotify: "Spotify"
        case .musicbrainz: "MusicBrainz"
        }
    }

    /// The service's logo (Simple Icons; a template image in the asset catalog).
    var logo: ImageResource {
        switch self {
        case .wikipedia: .serviceWikipedia
        case .bandcamp: .serviceBandcamp
        case .spotify: .serviceSpotify
        case .musicbrainz: .serviceMusicbrainz
        }
    }
}
