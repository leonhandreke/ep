import AVFoundation
import AudioToolbox

/// WebM (Matroska) with an Opus track, the audio yt-dlp keeps from YouTube. AudioToolbox
/// decodes Opus packets but reads no Matroska, so this demuxes just enough of it: the
/// segment's Info and Tracks, Cues for seeking (found through the SeekHead when they come
/// after the clusters), and the clusters' SimpleBlocks and BlockGroups with any lacing.
/// Without Cues a seek guesses the byte offset from the duration and finds the next
/// cluster from there.
nonisolated final class WebMOpusDecoder: PCMDecoder, @unchecked Sendable {
    let format: AVAudioFormat
    /// The segment's duration, if it says.
    let duration: Double?

    private let source: HTTPRangeSource
    private let reader: EBMLReader
    private let track: UInt64
    private let timecodeScale: Double
    private let segmentStart: Int64
    private let firstCluster: Int64
    /// Cue points: (time in timecode units, cluster position relative to the segment).
    private let cues: [(time: Int64, position: Int64)]
    private let converter: AVAudioConverter
    private let inputFormat: AVAudioFormat

    private var cursor: Int64
    private var clusterTime: Int64 = 0
    private var pending: [(data: Data, time: Double)] = []
    private var skipBefore: Double?
    private var ended = false
    private var failure: (any Error)?

    init(source: HTTPRangeSource) throws {
        self.source = source
        reader = EBMLReader(source: source)
        guard let header = try reader.element(at: 0), header.id == EBML.header, let headerSize = header.size,
            let segment = try reader.element(at: header.dataStart + headerSize), segment.id == EBML.segment
        else { throw DecoderError("Not a WebM file") }
        segmentStart = segment.dataStart

        var scale: UInt64 = 1_000_000
        var duration: Double?
        var audio: EBML.AudioTrack?
        var cuesPosition: Int64?
        var cues: [(Int64, Int64)] = []
        var offset = segment.dataStart
        var cluster: Int64?
        while offset < source.size, let element = try reader.element(at: offset) {
            if element.id == EBML.cluster {
                cluster = offset
                break
            }
            guard let size = element.size else { throw DecoderError("Unsupported WebM layout") }
            switch element.id {
            case EBML.info:
                let children = EBML.children(try reader.data(element))
                scale = children[EBML.timecodeScale].map(EBML.uint) ?? scale
                duration = children[EBML.duration].map(EBML.float)
            case EBML.tracks:
                audio = EBML.audioTrack(in: try reader.data(element))
            case EBML.seekHead:
                cuesPosition = EBML.seekPosition(of: EBML.cues, in: try reader.data(element))
            case EBML.cues:
                cues = EBML.cuePoints(in: try reader.data(element))
            default: break
            }
            offset = element.dataStart + size
        }
        guard let cluster else { throw DecoderError("WebM file without audio") }
        guard let audio else { throw DecoderError("WebM file without an audio track") }
        guard audio.codec == "A_OPUS" else { throw DecoderError("Unsupported codec \(audio.codec) in WebM") }
        guard audio.channels <= 2 else { throw DecoderError("Opus with more than two channels") }
        if cues.isEmpty, let cuesPosition, let element = try reader.element(at: segment.dataStart + cuesPosition),
            element.id == EBML.cues
        {
            cues = EBML.cuePoints(in: try reader.data(element))
        }

        track = audio.number
        timecodeScale = Double(scale)
        firstCluster = cluster
        cursor = cluster
        self.cues = cues.sorted { $0.0 < $1.0 }
        // Opus always decodes at 48 kHz, whatever rate the track names.
        var description = AudioStreamBasicDescription(
            mSampleRate: 48_000, mFormatID: kAudioFormatOpus, mFormatFlags: 0, mBytesPerPacket: 0,
            mFramesPerPacket: 960, mBytesPerFrame: 0, mChannelsPerFrame: UInt32(audio.channels), mBitsPerChannel: 0,
            mReserved: 0)
        guard let input = AVAudioFormat(streamDescription: &description),
            let output = AVAudioFormat(
                commonFormat: .pcmFormatFloat32, sampleRate: 48_000, channels: AVAudioChannelCount(audio.channels),
                interleaved: false),
            let converter = AVAudioConverter(from: input, to: output)
        else { throw DecoderError("Opus decoder unavailable") }
        inputFormat = input
        format = output
        self.converter = converter
        self.duration = duration.map { $0 * Double(scale) / 1e9 }
    }

    // MARK: - Seeking

    func seek(to seconds: Double) throws -> Double {
        let target = max(seconds, 0)
        cursor = try clusterPosition(before: target)
        pending = []
        ended = false
        failure = nil
        // Opus needs 80 ms of pre-roll to converge after a jump; what comes before the
        // target is decoded but not played (to the packet).
        skipBefore = target > 0 ? target - 0.08 : nil
        converter.reset()
        return max(target - 0.08, 0)
    }

    private func clusterPosition(before seconds: Double) throws -> Int64 {
        let ticks = Int64(seconds * 1e9 / timecodeScale)
        if !cues.isEmpty {
            let cue = cues.last { $0.time <= ticks } ?? cues[0]
            return segmentStart + cue.position
        }
        // No cues: guess from the duration, then step back until the cluster found
        // starts before the target.
        guard let duration, duration > 0, seconds > 0 else { return firstCluster }
        var fraction = seconds / duration
        for _ in 0..<6 {
            let guess = firstCluster + Int64(Double(source.size - firstCluster) * max(fraction, 0))
            if let found = try reader.cluster(atOrAfter: guess), found.time <= ticks {
                return found.offset
            }
            fraction -= 0.05
            if fraction <= 0 { break }
        }
        return firstCluster
    }

    // MARK: - Decoding

    func read(into buffer: AVAudioPCMBuffer) throws {
        buffer.frameLength = 0
        guard !ended else { return }
        var error: NSError?
        let status = converter.convert(to: buffer, error: &error) { [self] _, status in
            guard let packet = nextPacketForDecoder() else {
                status.pointee = .endOfStream
                return nil
            }
            status.pointee = .haveData
            return packet
        }
        if let failure { throw failure }
        if let error { throw error }
        if status == .endOfStream || status == .error { ended = true }
        if status == .error { throw DecoderError("Decoding Opus failed") }
    }

    /// The next audio packet as a compressed buffer, skipping what a seek jumped over.
    private func nextPacketForDecoder() -> AVAudioCompressedBuffer? {
        do {
            while let (data, time) = try nextPacket() {
                if let skip = skipBefore, time < skip { continue }
                skipBefore = nil
                let buffer = AVAudioCompressedBuffer(
                    format: inputFormat, packetCapacity: 1, maximumPacketSize: max(data.count, 1))
                data.withUnsafeBytes { buffer.data.copyMemory(from: $0.baseAddress!, byteCount: data.count) }
                buffer.byteLength = UInt32(data.count)
                buffer.packetCount = 1
                buffer.packetDescriptions?.pointee = AudioStreamPacketDescription(
                    mStartOffset: 0, mVariableFramesInPacket: 0, mDataByteSize: UInt32(data.count))
                return buffer
            }
        } catch {
            failure = error
        }
        return nil
    }

    /// Walks the clusters element by element: enters clusters and block groups, reads
    /// timecodes and blocks of the audio track, skips everything else.
    private func nextPacket() throws -> (Data, Double)? {
        if !pending.isEmpty { return pending.removeFirst() }
        while cursor < source.size {
            guard let element = try reader.element(at: cursor) else { return nil }
            switch element.id {
            case EBML.cluster, EBML.blockGroup:
                cursor = element.dataStart
                continue
            default: break
            }
            guard let size = element.size else { throw DecoderError("Unsupported WebM layout") }
            cursor = element.dataStart + size
            switch element.id {
            case EBML.timecode:
                clusterTime = Int64(EBML.uint(try reader.data(element)))
            case EBML.simpleBlock, EBML.block:
                let block = try reader.data(element)
                guard let parsed = EBML.block(block), parsed.track == track else { continue }
                let time = Double(clusterTime + Int64(parsed.timecode)) * timecodeScale / 1e9
                pending = parsed.frames.map { ($0, time) }
                if !pending.isEmpty { return pending.removeFirst() }
            default:
                continue
            }
        }
        return nil
    }
}

// MARK: - EBML

/// Reads EBML elements from the source.
nonisolated struct EBMLReader {
    struct Element {
        var id: UInt32
        var dataStart: Int64
        /// nil: unknown size (allowed for segments and clusters).
        var size: Int64?
    }

    let source: HTTPRangeSource

    /// The element header at `offset`; nil at the end of the file.
    func element(at offset: Int64) throws -> Element? {
        let bytes = [UInt8](try source.read(at: offset, count: 12))
        guard let (id, idLength) = EBML.vint(bytes, at: 0, keepMarker: true),
            let (size, sizeLength) = EBML.vint(bytes, at: idLength, keepMarker: false)
        else { return nil }
        let unknown = size == (UInt64(1) << (7 * UInt64(sizeLength))) - 1
        return Element(
            id: UInt32(truncatingIfNeeded: id), dataStart: offset + Int64(idLength + sizeLength),
            size: unknown ? nil : Int64(size))
    }

    func data(_ element: Element) throws -> Data {
        guard let size = element.size, size < 64 * 1024 * 1024 else { throw DecoderError("WebM element too large") }
        return try source.read(at: element.dataStart, count: Int(size))
    }

    /// The first cluster starting at or after `offset`, with its timecode.
    func cluster(atOrAfter offset: Int64) throws -> (offset: Int64, time: Int64)? {
        var from = offset
        let marker: [UInt8] = [0x1F, 0x43, 0xB6, 0x75]
        while from < source.size {
            let window = [UInt8](try source.read(at: from, count: 256 * 1024))
            guard window.count >= 4 else { return nil }
            var index = 0
            while index + 4 <= window.count {
                if window[index] == marker[0], Array(window[index..<index + 4]) == marker {
                    let position = from + Int64(index)
                    if let cluster = try element(at: position), let first = try element(at: cluster.dataStart),
                        first.id == EBML.timecode
                    {
                        return (position, Int64(EBML.uint(try data(first))))
                    }
                }
                index += 1
            }
            from += Int64(window.count - 3)
        }
        return nil
    }
}

nonisolated enum EBML {
    static let header: UInt32 = 0x1A45_DFA3
    static let segment: UInt32 = 0x1853_8067
    static let seekHead: UInt32 = 0x114D_9B74
    static let seek: UInt32 = 0x4DBB
    static let seekID: UInt32 = 0x53AB
    static let seekPosition: UInt32 = 0x53AC
    static let info: UInt32 = 0x1549_A966
    static let timecodeScale: UInt32 = 0x2AD7B1
    static let duration: UInt32 = 0x4489
    static let tracks: UInt32 = 0x1654_AE6B
    static let trackEntry: UInt32 = 0xAE
    static let trackNumber: UInt32 = 0xD7
    static let trackType: UInt32 = 0x83
    static let codecID: UInt32 = 0x86
    static let audio: UInt32 = 0xE1
    static let channels: UInt32 = 0x9F
    static let cues: UInt32 = 0x1C53_BB6B
    static let cuePoint: UInt32 = 0xBB
    static let cueTime: UInt32 = 0xB3
    static let cueTrackPositions: UInt32 = 0xB7
    static let cueClusterPosition: UInt32 = 0xF1
    static let cluster: UInt32 = 0x1F43_B675
    static let timecode: UInt32 = 0xE7
    static let simpleBlock: UInt32 = 0xA3
    static let blockGroup: UInt32 = 0xA0
    static let block: UInt32 = 0xA1

    struct AudioTrack {
        var number: UInt64
        var codec: String
        var channels: Int
    }

    /// A variable-length integer at `index`: (value, length). IDs keep their length
    /// marker; sizes drop it.
    static func vint(_ bytes: [UInt8], at index: Int, keepMarker: Bool) -> (UInt64, Int)? {
        guard index < bytes.count, bytes[index] != 0 else { return nil }
        let length = bytes[index].leadingZeroBitCount + 1
        guard index + length <= bytes.count else { return nil }
        var value = UInt64(keepMarker ? bytes[index] : bytes[index] & (0xFF >> length))
        for byte in bytes[(index + 1)..<(index + length)] {
            value = value << 8 | UInt64(byte)
        }
        return (value, length)
    }

    /// The direct children of a master element's data, by ID (the first of each ID).
    static func children(_ data: Data) -> [UInt32: Data] {
        var result: [UInt32: Data] = [:]
        for (id, child) in list(data) where result[id] == nil { result[id] = child }
        return result
    }

    /// The direct children of a master element's data, in order.
    static func list(_ data: Data) -> [(UInt32, Data)] {
        let bytes = [UInt8](data)
        var index = 0
        var result: [(UInt32, Data)] = []
        while let (id, idLength) = vint(bytes, at: index, keepMarker: true),
            let (size, sizeLength) = vint(bytes, at: index + idLength, keepMarker: false)
        {
            let start = index + idLength + sizeLength
            let end = min(start + Int(min(size, UInt64(bytes.count))), bytes.count)
            result.append((UInt32(truncatingIfNeeded: id), Data(bytes[start..<end])))
            index = end
        }
        return result
    }

    static func uint(_ data: Data) -> UInt64 {
        data.prefix(8).reduce(0) { $0 << 8 | UInt64($1) }
    }

    static func float(_ data: Data) -> Double {
        switch data.count {
        case 4: Double(Float(bitPattern: UInt32(truncatingIfNeeded: uint(data))))
        case 8: Double(bitPattern: uint(data))
        default: 0
        }
    }

    static func audioTrack(in tracks: Data) -> AudioTrack? {
        for (id, entry) in list(tracks) where id == trackEntry {
            let fields = children(entry)
            guard fields[trackType].map(uint) == 2, let number = fields[trackNumber].map(uint) else { continue }
            let codec = fields[codecID].map { String(decoding: $0, as: UTF8.self) } ?? ""
            let channels = fields[audio].flatMap { children($0)[EBML.channels] }.map(uint) ?? 2
            return AudioTrack(
                number: number, codec: codec.trimmingCharacters(in: .controlCharacters), channels: Int(channels))
        }
        return nil
    }

    static func seekPosition(of target: UInt32, in seekHead: Data) -> Int64? {
        for (id, entry) in list(seekHead) where id == seek {
            let fields = children(entry)
            if fields[seekID].map(uint) == UInt64(target), let position = fields[seekPosition].map(uint) {
                return Int64(position)
            }
        }
        return nil
    }

    static func cuePoints(in cues: Data) -> [(Int64, Int64)] {
        list(cues).compactMap { id, point in
            guard id == cuePoint else { return nil }
            let fields = children(point)
            guard let time = fields[cueTime].map(uint),
                let position = fields[cueTrackPositions].flatMap({ children($0)[cueClusterPosition] }).map(uint)
            else { return nil }
            return (Int64(time), Int64(position))
        }
    }

    /// A (Simple)Block: track number, timecode relative to its cluster, and its frames
    /// (unlaced, or Xiph, fixed-size or EBML laced).
    static func block(_ data: Data) -> (track: UInt64, timecode: Int16, frames: [Data])? {
        let bytes = [UInt8](data)
        guard let (track, length) = vint(bytes, at: 0, keepMarker: false), bytes.count >= length + 3 else { return nil }
        let timecode = Int16(bitPattern: UInt16(bytes[length]) << 8 | UInt16(bytes[length + 1]))
        let flags = bytes[length + 2]
        var index = length + 3
        let lacing = (flags >> 1) & 0x03
        guard lacing != 0 else { return (track, timecode, [Data(bytes[index...])]) }
        guard index < bytes.count else { return nil }
        let count = Int(bytes[index]) + 1
        index += 1
        var sizes: [Int] = []
        switch lacing {
        case 1:  // Xiph
            for _ in 0..<(count - 1) {
                var size = 0
                while index < bytes.count {
                    let byte = Int(bytes[index])
                    index += 1
                    size += byte
                    if byte != 255 { break }
                }
                sizes.append(size)
            }
        case 3:  // EBML
            guard let (first, firstLength) = vint(bytes, at: index, keepMarker: false) else { return nil }
            index += firstLength
            sizes.append(Int(first))
            for _ in 0..<max(count - 2, 0) {
                guard let (raw, rawLength) = vint(bytes, at: index, keepMarker: false) else { return nil }
                index += rawLength
                let bias = (1 << (7 * rawLength - 1)) - 1
                sizes.append(sizes.last! + Int(raw) - bias)
            }
        default:  // fixed
            let each = (bytes.count - index) / count
            sizes = Array(repeating: each, count: count - 1)
        }
        let rest = bytes.count - index - sizes.reduce(0, +)
        guard rest >= 0, sizes.allSatisfy({ $0 >= 0 }) else { return nil }
        sizes.append(rest)
        var frames: [Data] = []
        for size in sizes {
            frames.append(Data(bytes[index..<index + size]))
            index += size
        }
        return (track, timecode, frames)
    }
}
