import AVFoundation
import AudioToolbox

/// Decodes a file to PCM for `DecodingEngine`: 32-bit float, deinterleaved, at the file's
/// own rate and channel count (`format`).
nonisolated protocol PCMDecoder: AnyObject {
    var format: AVAudioFormat { get }
    /// Positions decoding at `seconds`; returns where decoding really starts.
    func seek(to seconds: Double) throws -> Double
    /// Fills `buffer` (up to its capacity); a `frameLength` of 0 means the end.
    func read(into buffer: AVAudioPCMBuffer) throws
}

nonisolated struct DecoderError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }

    static func check(_ status: OSStatus, _ what: String) throws {
        guard status == noErr else { throw DecoderError("\(what) failed (\(status))") }
    }
}

/// Ogg Opus and Ogg Vorbis through AudioToolbox, which reads Ogg since macOS 15 (`afinfo`
/// shows "Oggf"), from an `HTTPRangeSource` via AudioFile's read callbacks. Opening needs
/// a few hundred kilobytes spread over the file (AudioToolbox looks for the last page to
/// learn the length); seeking then reads about a kilobyte.
nonisolated final class AudioFileDecoder: PCMDecoder {
    let format: AVAudioFormat
    private let source: HTTPRangeSource
    private var file: AudioFileID?
    private var ext: ExtAudioFileRef?

    init(source: HTTPRangeSource) throws {
        self.source = source
        let context = Unmanaged.passUnretained(source).toOpaque()
        var file: AudioFileID?
        try DecoderError.check(
            AudioFileOpenWithCallbacks(context, Self.readProc, nil, Self.sizeProc, nil, 0, &file), "Opening the file")
        self.file = file
        var ext: ExtAudioFileRef?
        try DecoderError.check(ExtAudioFileWrapAudioFileID(file!, false, &ext), "Reading the file")
        self.ext = ext

        var fileFormat = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        try DecoderError.check(
            ExtAudioFileGetProperty(ext!, kExtAudioFileProperty_FileDataFormat, &size, &fileFormat),
            "Reading the format")
        guard
            let format = AVAudioFormat(
                commonFormat: .pcmFormatFloat32, sampleRate: fileFormat.mSampleRate,
                channels: max(fileFormat.mChannelsPerFrame, 1), interleaved: false)
        else { throw DecoderError("Unsupported format") }
        self.format = format
        var client = format.streamDescription.pointee
        try DecoderError.check(
            ExtAudioFileSetProperty(
                ext!, kExtAudioFileProperty_ClientDataFormat, UInt32(MemoryLayout<AudioStreamBasicDescription>.size),
                &client), "Choosing the output format")
    }

    deinit {
        if let ext { ExtAudioFileDispose(ext) }
        if let file { AudioFileClose(file) }
    }

    func seek(to seconds: Double) throws -> Double {
        let frame = Int64(max(seconds, 0) * format.sampleRate)
        try DecoderError.check(ExtAudioFileSeek(ext!, frame), "Seeking")
        return Double(frame) / format.sampleRate
    }

    func read(into buffer: AVAudioPCMBuffer) throws {
        var frames = buffer.frameCapacity
        buffer.frameLength = buffer.frameCapacity
        let status = ExtAudioFileRead(ext!, &frames, buffer.mutableAudioBufferList)
        buffer.frameLength = status == noErr ? frames : 0
        try DecoderError.check(status, "Decoding")
    }

    private static let readProc: AudioFile_ReadProc = { context, position, count, buffer, actual in
        let source = Unmanaged<HTTPRangeSource>.fromOpaque(context).takeUnretainedValue()
        guard let data = try? source.read(at: position, count: Int(count)) else {
            actual.pointee = 0
            return kAudioFileUnspecifiedError
        }
        data.copyBytes(to: buffer.assumingMemoryBound(to: UInt8.self), count: data.count)
        actual.pointee = UInt32(data.count)
        return noErr
    }

    private static let sizeProc: AudioFile_GetSizeProc = { context in
        Unmanaged<HTTPRangeSource>.fromOpaque(context).takeUnretainedValue().size
    }
}
