import AVFoundation
import Foundation

/// What the player needs from an audio engine: play one file from a time, report the
/// time, and say when the file ended. Times are seconds in the file.
protocol PlaybackEngine: AnyObject {
    /// Prepares `url`, a file of `mediaType`, paused at `seconds`.
    func load(_ url: URL, mediaType: String, at seconds: Double) async throws
    func play()
    func pause()
    func seek(to seconds: Double)
    /// Stops and lets go of the file.
    func stop()
    var currentTime: Double { get }
    var volume: Float { get set }
    /// The file played to its end.
    var onEnd: (() -> Void)? { get set }
    /// Playback failed after loading (network, decoding).
    var onFailure: ((String) -> Void)? { get set }
}

/// The app's engine: AVPlayer for what AVFoundation plays (MP3, AAC/ALAC, FLAC, WAV,
/// AIFF), `DecodingEngine` for Ogg (Opus, Vorbis) and WebM (Opus), which AVPlayer cannot
/// decode. The file's media type (from the playback info) decides.
final class AudioOutput: PlaybackEngine {
    private let avPlayer = AVPlayerEngine()
    private let decoding = DecodingEngine()
    private var current: (any PlaybackEngine)?
    private let session: URLSession

    var onEnd: (() -> Void)?
    var onFailure: ((String) -> Void)?
    var volume: Float = 1 {
        didSet {
            avPlayer.volume = volume
            decoding.volume = volume
        }
    }

    init(session: URLSession = .shared) {
        self.session = session
        for engine in [avPlayer, decoding] as [any PlaybackEngine] {
            engine.onEnd = { [weak self, weak engine] in
                guard let self, engine === self.current else { return }
                self.onEnd?()
            }
            engine.onFailure = { [weak self, weak engine] message in
                guard let self, engine === self.current else { return }
                self.onFailure?(message)
            }
        }
    }

    func load(_ url: URL, mediaType: String, at seconds: Double) async throws {
        current?.stop()
        let container = AudioContainer(mediaType: mediaType)
        current = container == .other ? avPlayer : decoding
        if container == .other {
            try await avPlayer.load(url, mediaType: mediaType, at: seconds)
        } else {
            let source = try await HTTPRangeSource.open(url, session: session)
            try Task.checkCancellation()
            try await decoding.load(source, container: container, at: seconds)
        }
    }

    func play() { current?.play() }
    func pause() { current?.pause() }
    func seek(to seconds: Double) { current?.seek(to: seconds) }
    func stop() {
        current?.stop()
        current = nil
    }

    var currentTime: Double { current?.currentTime ?? 0 }
}

// MARK: - AVPlayer

final class AVPlayerEngine: PlaybackEngine {
    private let player = AVPlayer()
    private var endObserver: (any NSObjectProtocol)?
    private var statusObservation: NSKeyValueObservation?

    var onEnd: (() -> Void)?
    var onFailure: ((String) -> Void)?
    var volume: Float {
        get { player.volume }
        set { player.volume = newValue }
    }

    func load(_ url: URL, mediaType: String, at seconds: Double) async throws {
        stop()
        let asset = AVURLAsset(url: url)
        guard try await asset.load(.isPlayable) else { throw DecoderError("This file’s format cannot be played") }
        // A newer load took over while this one waited: leave the player to it.
        try Task.checkCancellation()
        let item = AVPlayerItem(asset: asset)
        player.replaceCurrentItem(with: item)
        endObserver = NotificationCenter.default.addObserver(
            forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.onEnd?() }
        }
        statusObservation = item.observe(\.status) { [weak self] item, _ in
            guard item.status == .failed else { return }
            let message = item.error?.localizedDescription ?? "Playback failed"
            Task { @MainActor in self?.onFailure?(message) }
        }
        if seconds > 0 {
            await player.seek(
                to: CMTime(seconds: seconds, preferredTimescale: 1000), toleranceBefore: .zero, toleranceAfter: .zero)
        }
    }

    func play() { player.play() }
    func pause() { player.pause() }

    func seek(to seconds: Double) {
        player.seek(
            to: CMTime(seconds: max(seconds, 0), preferredTimescale: 1000), toleranceBefore: .zero,
            toleranceAfter: .zero)
    }

    func stop() {
        player.pause()
        player.replaceCurrentItem(with: nil)
        if let endObserver { NotificationCenter.default.removeObserver(endObserver) }
        endObserver = nil
        statusObservation = nil
    }

    var currentTime: Double {
        let time = player.currentTime().seconds
        return time.isFinite ? time : 0
    }
}

// MARK: - Decoding engine

/// AVAudioEngine fed by a `PCMDecoder` (AudioToolbox for Ogg, `WebMOpusDecoder` for
/// WebM), reading the file through an `HTTPRangeSource`. Decoding runs on a serial queue
/// that keeps about two seconds scheduled on the player node.
final class DecodingEngine: PlaybackEngine {
    private let core = DecodingCore()

    var onEnd: (() -> Void)?
    var onFailure: ((String) -> Void)?
    var volume: Float = 1 {
        didSet { core.node.volume = volume }
    }

    init() {
        core.onEnd = { [weak self] in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.onEnd?() } }
        }
        core.onFailure = { [weak self] message in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.onFailure?(message) } }
        }
    }

    func load(_ url: URL, mediaType: String, at seconds: Double) async throws {
        let source = try await HTTPRangeSource.open(url)
        try await load(source, container: AudioContainer(mediaType: mediaType), at: seconds)
    }

    func load(_ source: HTTPRangeSource, container: AudioContainer, at seconds: Double) async throws {
        try await core.load(source, container: container, at: seconds)
    }

    func play() { core.play() }
    func pause() { core.pause() }
    func seek(to seconds: Double) { core.seek(to: seconds) }
    func stop() { core.stop() }
    var currentTime: Double { core.currentTime }
}

/// The thread-safe part of `DecodingEngine`. The decoder is used only on `queue`;
/// `lock` guards what the main thread reads (time, generation).
private nonisolated final class DecodingCore: @unchecked Sendable {
    let node = AVAudioPlayerNode()
    var onEnd: (@Sendable () -> Void)?
    var onFailure: (@Sendable (String) -> Void)?

    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "de.ndreke.ep.decoding", qos: .userInitiated)
    private let lock = NSLock()

    // Guarded by `lock`.
    private var generation = 0
    private var base: Double = 0
    private var scheduledSeconds: Double = 0
    private var pendingSeek: Double?
    private var source: HTTPRangeSource?
    private var playing = false

    // Used on `queue` only.
    private var decoder: (any PCMDecoder)?
    private var connectedFormat: AVAudioFormat?
    private var pending = 0
    private var decodedToEnd = false
    private var endReported = false

    private static let bufferSeconds = 0.5
    private static let buffersAhead = 4

    init() {
        engine.attach(node)
        NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil
        ) { [weak self] _ in
            // The output device changed: the engine stopped; carry on where it was.
            guard let self, lock.withLock({ playing }) else { return }
            try? engine.start()
            node.play()
        }
    }

    func load(_ source: HTTPRangeSource, container: AudioContainer, at seconds: Double) async throws {
        let generation = lock.withLock {
            self.generation += 1
            self.source?.cancel()
            self.source = source
            playing = false
            base = seconds
            scheduledSeconds = 0
            pendingSeek = nil
            return self.generation
        }
        node.stop()
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
            queue.async { [self] in
                do {
                    let decoder: any PCMDecoder =
                        container == .matroska
                        ? try WebMOpusDecoder(source: source) : try AudioFileDecoder(source: source)
                    let start = seconds > 0 ? try decoder.seek(to: seconds) : 0
                    guard lock.withLock({ self.generation == generation }) else {
                        return continuation.resume(throwing: CancellationError())
                    }
                    if connectedFormat != decoder.format {
                        engine.stop()
                        engine.connect(node, to: engine.mainMixerNode, format: decoder.format)
                        connectedFormat = decoder.format
                    }
                    self.decoder = decoder
                    lock.withLock { base = start }
                    restartQueue()
                    fill(generation)
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    func play() {
        lock.withLock { playing = true }
        if !engine.isRunning {
            engine.prepare()
            do {
                try engine.start()
            } catch {
                onFailure?(error.localizedDescription)
                return
            }
        }
        node.play()
    }

    func pause() {
        lock.withLock { playing = false }
        node.pause()
    }

    func seek(to seconds: Double) {
        let generation = lock.withLock {
            self.generation += 1
            pendingSeek = seconds
            scheduledSeconds = 0
            return self.generation
        }
        node.stop()
        queue.async { [self] in
            guard let decoder, lock.withLock({ self.generation == generation }) else { return }
            do {
                let start = try decoder.seek(to: seconds)
                lock.withLock {
                    base = start
                    pendingSeek = nil
                }
                restartQueue()
                fill(generation)
                if lock.withLock({ playing && self.generation == generation }) { node.play() }
            } catch {
                report(error, generation)
            }
        }
    }

    func stop() {
        lock.withLock {
            generation += 1
            playing = false
            source?.cancel()
            source = nil
        }
        node.stop()
        queue.async { [self] in decoder = nil }
    }

    var currentTime: Double {
        lock.withLock {
            if let pendingSeek { return pendingSeek }
            guard let renderTime = node.lastRenderTime, renderTime.isSampleTimeValid,
                let playerTime = node.playerTime(forNodeTime: renderTime)
            else { return base }
            let played = max(Double(playerTime.sampleTime) / playerTime.sampleRate, 0)
            return base + min(played, scheduledSeconds)
        }
    }

    // MARK: - On the queue

    private func restartQueue() {
        pending = 0
        decodedToEnd = false
        endReported = false
    }

    /// Decodes and schedules until enough is ahead.
    private func fill(_ generation: Int) {
        guard let decoder else { return }
        while pending < Self.buffersAhead, !decodedToEnd {
            let capacity = AVAudioFrameCount(decoder.format.sampleRate * Self.bufferSeconds)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: decoder.format, frameCapacity: capacity) else { return }
            do {
                try decoder.read(into: buffer)
            } catch {
                return report(error, generation)
            }
            guard lock.withLock({ self.generation == generation }) else { return }
            guard buffer.frameLength > 0 else {
                decodedToEnd = true
                break
            }
            pending += 1
            lock.withLock { scheduledSeconds += Double(buffer.frameLength) / decoder.format.sampleRate }
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                guard let self else { return }
                queue.async {
                    guard self.lock.withLock({ self.generation == generation }) else { return }
                    self.pending -= 1
                    self.fill(generation)
                }
            }
        }
        if decodedToEnd, pending == 0, !endReported {
            endReported = true
            onEnd?()
        }
    }

    private func report(_ error: any Error, _ generation: Int) {
        guard lock.withLock({ self.generation == generation }) else { return }
        decodedToEnd = true
        if case HTTPRangeSource.Failure.cancelled = error { return }
        onFailure?(String(describing: error))
    }
}
