import AVFoundation
import Foundation
import Testing

@testable import EP

/// Serves files from memory with HTTP Range, like `GET /sources/{id}/stream`, and counts
/// requests.
nonisolated final class RangeFileServer: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    nonisolated(unsafe) private static var files: [String: Data] = [:]

    static func serve(_ data: Data, at path: String) {
        lock.withLock { files[path] = data }
    }

    static var session: URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RangeFileServer.self]
        return URLSession(configuration: configuration)
    }

    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "files.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        let url = request.url!
        guard let data = Self.lock.withLock({ Self.files[url.path] }) else {
            let response = HTTPURLResponse(url: url, statusCode: 404, httpVersion: "HTTP/1.1", headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        var status = 200
        var body = data
        var headers = ["Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"]
        if let range = request.value(forHTTPHeaderField: "Range"), range.hasPrefix("bytes=") {
            let bounds = range.dropFirst(6).split(separator: "-", omittingEmptySubsequences: false)
            let from = Int(bounds[0]) ?? 0
            let to = min(Int(bounds[1]) ?? data.count - 1, data.count - 1)
            body = data.subdata(in: from..<(to + 1))
            status = 206
            headers["Content-Range"] = "bytes \(from)-\(to)/\(data.count)"
        }
        headers["Content-Length"] = String(body.count)
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
}

/// Test audio made with ffmpeg in a temporary directory: 3 s of silence, then 3 s of a
/// 440 Hz tone, so where a seek lands can be heard in the samples.
nonisolated enum AudioFixtures {
    static let ffmpeg = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"].first {
        FileManager.default.isExecutableFile(atPath: $0)
    }

    static let silenceThenTone = "sine=frequency=440:duration=6,volume='if(lt(t,3),0,1)':eval=frame"

    static func make(_ name: String, _ arguments: [String], source: String = silenceThenTone) throws -> Data {
        let directory = FileManager.default.temporaryDirectory.appending(path: "ep-audio-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let output = directory.appending(path: name)
        let process = Process()
        process.executableURL = URL(fileURLWithPath: ffmpeg!)
        process.arguments =
            [
                "-v", "error", "-y", "-f", "lavfi", "-i", source, "-ac", "2",
            ] + arguments + [output.path]
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw DecoderError("ffmpeg failed for \(name)") }
        return try Data(contentsOf: output)
    }

    static let formats: [(name: String, arguments: [String])] = [
        ("opus.ogg", ["-c:a", "libopus"]),
        ("vorbis.ogg", ["-c:a", "vorbis", "-strict", "-2"]),
        ("opus.webm", ["-c:a", "libopus"]),
        ("cues-first.webm", ["-c:a", "libopus", "-cues_to_front", "1", "-reserve_index_space", "4096"]),
        // No cues, unknown sizes: a seek finds its way from the first cluster.
        ("live.webm", ["-c:a", "libopus", "-live", "1"]),
    ]
}

private func peak(_ buffer: AVAudioPCMBuffer) -> Float {
    guard let samples = buffer.floatChannelData?[0] else { return 0 }
    return (0..<Int(buffer.frameLength)).reduce(Float(0)) { max($0, abs(samples[$1])) }
}

@Suite(.enabled(if: AudioFixtures.ffmpeg != nil, "needs ffmpeg"))
struct AudioDecodingTests {
    private func decoder(for data: Data, name: String) async throws -> (any PCMDecoder, HTTPRangeSource) {
        RangeFileServer.serve(data, at: "/\(name)")
        let source = try await HTTPRangeSource.open(
            URL(string: "http://files.test/\(name)")!, session: RangeFileServer.session)
        let container = AudioContainer(mediaType: name.hasSuffix(".ogg") ? "audio/ogg" : "audio/webm")
        let decoder: any PCMDecoder =
            container == .matroska ? try WebMOpusDecoder(source: source) : try AudioFileDecoder(source: source)
        return (decoder, source)
    }

    @Test(arguments: AudioFixtures.formats.map(\.name))
    func decodesAndSeeksOverHTTPRange(_ name: String) async throws {
        let arguments = AudioFixtures.formats.first { $0.name == name }!.arguments
        let data = try AudioFixtures.make(name, arguments)
        let (decoder, _) = try await decoder(for: data, name: name)
        #expect(decoder.format.channelCount == 2)
        let buffer = try #require(AVAudioPCMBuffer(pcmFormat: decoder.format, frameCapacity: 4800))

        // The start is silent.
        try decoder.read(into: buffer)
        #expect(buffer.frameLength > 0)
        #expect(peak(buffer) < 0.01)

        // Past 3 s the tone plays; back at 1 s it is silent again.
        let landed = try decoder.seek(to: 4.5)
        #expect(abs(landed - 4.5) < 0.1)
        try decoder.read(into: buffer)
        #expect(peak(buffer) > 0.05)
        _ = try decoder.seek(to: 1.0)
        try decoder.read(into: buffer)
        #expect(peak(buffer) < 0.01)

        // To the end: about 1.5 s more after a seek to 4.5 s.
        _ = try decoder.seek(to: 4.5)
        var frames = 0
        repeat {
            try decoder.read(into: buffer)
            frames += Int(buffer.frameLength)
        } while buffer.frameLength > 0
        #expect(abs(Double(frames) / decoder.format.sampleRate - 1.5) < 0.15)
    }

    @Test func aLongFileOpensAndSeeksWithFewRequests() async throws {
        // 10 minutes of Opus (~5 MB): reading it all would take ~10 sequential chunks.
        let data = try AudioFixtures.make(
            "long.webm", ["-c:a", "libopus", "-b:a", "64k"], source: "sine=frequency=440:duration=600")
        let (decoder, source) = try await decoder(for: data, name: "long.webm")
        let buffer = try #require(AVAudioPCMBuffer(pcmFormat: decoder.format, frameCapacity: 4800))
        _ = try decoder.seek(to: 450)
        try decoder.read(into: buffer)
        #expect(buffer.frameLength > 0)
        #expect(source.requestCount < 8)
    }

    @Test func aMissingFileIsAnError() async throws {
        await #expect(throws: HTTPRangeSource.Failure.status(404)) {
            try await HTTPRangeSource.open(URL(string: "http://files.test/nothing")!, session: RangeFileServer.session)
        }
    }

    @Test func choosesTheContainerFromTheMediaType() {
        #expect(AudioContainer(mediaType: "audio/ogg") == .ogg)
        #expect(AudioContainer(mediaType: "Audio/Ogg; codecs=opus") == .ogg)
        #expect(AudioContainer(mediaType: "audio/webm") == .matroska)
        #expect(AudioContainer(mediaType: "audio/x-matroska") == .matroska)
        #expect(AudioContainer(mediaType: "audio/flac") == .other)
        #expect(AudioContainer(mediaType: "audio/mpeg") == .other)
        #expect(AudioContainer(mediaType: "application/octet-stream") == .other)
        #expect(HTTPRangeSource.totalLength("bytes 0-65535/1234567") == 1_234_567)
    }
}

/// The decoding engine end to end (muted): plays from a time, reports time, seeks, ends.
@Suite(.enabled(if: AudioFixtures.ffmpeg != nil, "needs ffmpeg"), .serialized)
@MainActor
struct DecodingEngineTests {
    @Test(arguments: ["opus.ogg", "opus.webm"])
    func playsSeeksAndEnds(_ name: String) async throws {
        let arguments = AudioFixtures.formats.first { $0.name == name }!.arguments
        RangeFileServer.serve(try AudioFixtures.make(name, arguments), at: "/engine-\(name)")
        let source = try await HTTPRangeSource.open(
            URL(string: "http://files.test/engine-\(name)")!, session: RangeFileServer.session)
        let engine = DecodingEngine()
        engine.volume = 0
        var ended = false
        engine.onEnd = { ended = true }

        try await engine.load(source, container: name.hasSuffix(".ogg") ? .ogg : .matroska, at: 2)
        engine.play()
        try await Task.sleep(for: .milliseconds(600))
        #expect(engine.currentTime > 2.3 && engine.currentTime < 3.2)

        engine.seek(to: 5.2)
        for _ in 0..<40 where !ended { try await Task.sleep(for: .milliseconds(100)) }
        #expect(ended)
        #expect(engine.currentTime > 5.5)
        engine.stop()
    }
}

/// Malformed Matroska must fail cleanly: an error or the end, never a crash or a spin.
struct WebMRobustnessTests {
    @Test func lacedBlocksWithHugeSizesAreRefused() {
        // Track 1, timecode 0, EBML lacing, 4 frames: sizes that would overflow a sum.
        let huge: [UInt8] = [0x81, 0x00, 0x00, 0x06, 0x03, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        #expect(EBML.block(Data(huge)) == nil)
        // Truncated everywhere: never a crash.
        let good: [UInt8] = [0x81, 0x00, 0x00, 0x02, 0x01, 0x02, 0xAA, 0xBB, 0xCC]  // Xiph, 2 frames
        for length in 0...good.count { _ = EBML.block(Data(good.prefix(length))) }
        #expect(EBML.block(Data(good))?.frames.count == 2)
    }

    @Test func oneFrameEBMLLacingIsOneFrame() throws {
        let block: [UInt8] = [0x81, 0x00, 0x00, 0x06, 0x00, 0xAA, 0xBB]
        #expect(try #require(EBML.block(Data(block))).frames == [Data([0xAA, 0xBB])])
    }

    @Test func absurdNumbersAreClamped() {
        #expect(EBML.int(Data(repeating: 0xFF, count: 8)) == Int64(EBML.limit))
        let cues = Data([0xBB, 0x8C, 0xB3, 0x88] + [UInt8](repeating: 0xFF, count: 8))  // truncated point
        _ = EBML.cuePoints(in: cues)
    }

    @Test(.enabled(if: AudioFixtures.ffmpeg != nil, "needs ffmpeg"))
    func corruptedFilesFailCleanly() async throws {
        let original = try AudioFixtures.make("fuzz.webm", ["-c:a", "libopus", "-b:a", "32k"])
        var random = SystemRandomNumberGenerator()
        for round in 0..<40 {
            var data = original
            switch round % 4 {
            case 0: data = data.prefix(Int.random(in: 0..<data.count, using: &random))  // truncated
            default:
                for _ in 0..<(round % 4) * 6 {
                    data[Int.random(in: 0..<min(data.count, round % 2 == 0 ? 400 : data.count), using: &random)] =
                        UInt8.random(in: 0...255, using: &random)
                }
            }
            let name = "fuzz\(round).webm"
            RangeFileServer.serve(data, at: "/\(name)")
            guard
                let source = try? await HTTPRangeSource.open(
                    URL(string: "http://files.test/\(name)")!, session: RangeFileServer.session),
                let decoder = try? WebMOpusDecoder(source: source),
                let buffer = AVAudioPCMBuffer(pcmFormat: decoder.format, frameCapacity: 4800)
            else { continue }
            for target in [0.0, 1.5, 2.9, 99, .infinity, .nan, -3] {
                _ = try? decoder.seek(to: target)
                for _ in 0..<2000 {  // bounded: a spin would fail here
                    guard (try? decoder.read(into: buffer)) != nil, buffer.frameLength > 0 else { break }
                }
            }
        }
    }
}
