import AppKit
import SwiftUI

/// Loads cover thumbnails (`cover_url` paths on the server) and keeps them in memory only
/// (plan: no disk cache): an `NSCache` of decoded images and an ephemeral URL session.
/// Concurrent requests for one URL share a download.
@Observable
final class CoverImages {
    @ObservationIgnored private let cache = NSCache<NSURL, NSImage>()
    @ObservationIgnored private var inFlight: [URL: Task<NSImage?, Never>] = [:]
    @ObservationIgnored private let session: URLSession

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        session = URLSession(configuration: configuration)
        cache.countLimit = 500
    }

    func cached(_ url: URL) -> NSImage? { cache.object(forKey: url as NSURL) }

    func image(for url: URL) async -> NSImage? {
        if let image = cached(url) { return image }
        if let task = inFlight[url] { return await task.value }
        let task = Task { [session] () -> NSImage? in
            guard let (data, response) = try? await session.data(from: url),
                (response as? HTTPURLResponse)?.statusCode == 200
            else { return nil }
            return NSImage(data: data)
        }
        inFlight[url] = task
        let image = await task.value
        inFlight[url] = nil
        if let image { cache.setObject(image, forKey: url as NSURL) }
        return image
    }

    /// Forgets everything (a different server).
    func removeAll() { cache.removeAllObjects() }
}

/// A rounded-square cover thumbnail with the design's inner hairline; a placeholder when
/// there is no cover or it cannot be loaded.
struct CoverThumbnail: View {
    let path: String?
    var size: CGFloat = Metrics.coverSmall
    var radius: CGFloat = Radius.coverSmall

    @Environment(CoverImages.self) private var covers
    @Environment(ServerConnection.self) private var connection
    @State private var image: NSImage?

    private var url: URL? {
        path.flatMap { URL(string: $0, relativeTo: connection.serverURL)?.absoluteURL }
    }

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: radius)
        Group {
            if let image = image ?? url.flatMap(covers.cached) {
                Image(nsImage: image).resizable().interpolation(.high).scaledToFill()
            } else {
                Palette.chip.overlay {
                    Image(systemName: "music.note")
                        .font(.system(size: size * 0.4))
                        .foregroundStyle(Palette.text3)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(shape)
        .overlay(shape.strokeBorder(Palette.coverHairline, lineWidth: Metrics.hairline))
        .accessibilityHidden(true)
        .task(id: url) {
            image = nil
            guard let url else { return }
            image = await covers.image(for: url)
        }
    }
}
