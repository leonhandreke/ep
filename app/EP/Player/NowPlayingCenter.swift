import AppKit
import MediaPlayer

/// The system's Now Playing (Control Center, the menu bar's Now Playing) and the media
/// keys, through MediaPlayer. The title follows a long file's current entry; the artwork
/// is the cover (album or episode image) the player bar shows, from `CoverImages`.
final class NowPlayingCenter {
    private weak var player: Player?
    private let covers: CoverImages
    private let connection: ServerConnection
    /// The artwork of `artworkPath`, once loaded.
    private var artworkPath: String?
    private var artwork: MPMediaItemArtwork?
    private var artworkTask: Task<Void, Never>?

    init(player: Player, covers: CoverImages, connection: ServerConnection) {
        self.player = player
        self.covers = covers
        self.connection = connection
        let commands = MPRemoteCommandCenter.shared()
        commands.playCommand.addTarget { [weak self] _ in self?.perform { $0.resume() } ?? .noActionableNowPlayingItem }
        commands.pauseCommand.addTarget { [weak self] _ in self?.perform { $0.pause() } ?? .noActionableNowPlayingItem }
        commands.togglePlayPauseCommand.addTarget { [weak self] _ in
            self?.perform { $0.togglePlayPause() } ?? .noActionableNowPlayingItem
        }
        commands.nextTrackCommand.addTarget { [weak self] _ in
            self?.perform { $0.next() } ?? .noActionableNowPlayingItem
        }
        commands.previousTrackCommand.addTarget { [weak self] _ in
            self?.perform { $0.previous() } ?? .noActionableNowPlayingItem
        }
        commands.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let event = event as? MPChangePlaybackPositionCommandEvent else { return .commandFailed }
            let seconds = event.positionTime
            return self?.perform { $0.seek(to: $0.scrubberRange.lowerBound + Int(seconds * 1000)) }
                ?? .noActionableNowPlayingItem
        }
    }

    /// MediaPlayer calls the handlers on the main thread (refused otherwise, never a crash).
    private nonisolated func perform(_ action: @MainActor (Player) -> Void) -> MPRemoteCommandHandlerStatus {
        guard Thread.isMainThread else { return .commandFailed }
        return MainActor.assumeIsolated {
            guard let player, player.current != nil else { return .noActionableNowPlayingItem }
            action(player)
            return .success
        }
    }

    func update(_ player: Player) {
        let center = MPNowPlayingInfoCenter.default()
        guard let item = player.current else {
            center.nowPlayingInfo = nil
            center.playbackState = .stopped
            return
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: player.title,
            MPMediaItemPropertyArtist: player.subtitle,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: Double(player.elapsedMS) / 1000,
            MPNowPlayingInfoPropertyPlaybackRate: player.isPlaying ? 1.0 : 0.0,
            MPNowPlayingInfoPropertyMediaType: MPNowPlayingInfoMediaType.audio.rawValue,
        ]
        let range = player.scrubberRange
        if range.upperBound > range.lowerBound {
            info[MPMediaItemPropertyPlaybackDuration] = Double(range.upperBound - range.lowerBound) / 1000
        }
        if case .episode(let episode) = item.kind {
            info[MPMediaItemPropertyAlbumTitle] = episode.name
        }
        loadArtwork(item.coverPath)
        if let artwork, artworkPath == item.coverPath {
            info[MPMediaItemPropertyArtwork] = artwork
        }
        center.nowPlayingInfo = info
        center.playbackState = player.isPlaying ? .playing : .paused
    }

    /// Loads the cover of what plays when it changed, then updates Now Playing with it.
    private func loadArtwork(_ path: String?) {
        guard path != artworkPath else { return }
        artworkPath = path
        artwork = nil
        artworkTask?.cancel()
        guard let path, let url = URL(string: path, relativeTo: connection.serverURL)?.absoluteURL else { return }
        artworkTask = Task { [weak self] in
            guard let image = await self?.covers.image(for: url), !Task.isCancelled, let self,
                artworkPath == path
            else { return }
            artwork = Self.artwork(image)
            if let player { update(player) }
        }
    }

    /// The handler may be called on any thread: it only hands out the loaded image.
    private nonisolated static func artwork(_ image: NSImage) -> MPMediaItemArtwork {
        return MPMediaItemArtwork(boundsSize: image.size) { _ in image }
    }
}
