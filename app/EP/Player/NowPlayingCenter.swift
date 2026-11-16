import MediaPlayer

/// The system's Now Playing (Control Center, the menu bar's Now Playing) and the media
/// keys, through MediaPlayer. The title follows a long file's current entry.
final class NowPlayingCenter {
    private weak var player: Player?

    init(player: Player) {
        self.player = player
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
        center.nowPlayingInfo = info
        center.playbackState = player.isPlaying ? .playing : .paused
    }
}
