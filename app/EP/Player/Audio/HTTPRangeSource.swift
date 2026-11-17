import Foundation

/// Random access to a file on the server through HTTP Range requests
/// (`GET /sources/{id}/stream`), for decoders that read like from a file: AudioToolbox's
/// read callbacks and the WebM demuxer.
///
/// Reads are synchronous and block the calling thread (the decoding queue, never the main
/// thread). The file is fetched in blocks kept in a small in-memory cache; a read that
/// continues where the last one ended fetches a larger chunk, so playing straight through
/// takes few requests while seeking stays cheap.
nonisolated final class HTTPRangeSource: @unchecked Sendable {
    enum Failure: Error, Equatable {
        case status(Int)
        case noLength
        case cancelled
        case timedOut
    }

    static let blockSize = 64 * 1024
    /// The longest a read waits for one Range request.
    static let readTimeout: DispatchTimeInterval = .seconds(120)
    /// Blocks fetched at once when reading straight on.
    static let sequentialBlocks = 8
    static let cacheLimit = 96

    let url: URL
    /// The whole file's length in bytes.
    let size: Int64

    private let session: URLSession
    private let lock = NSLock()
    private var blocks: [Int64: Data] = [:]
    private var recent: [Int64] = []
    private var lastBlock: Int64 = -2
    private var cancelled = false
    private var task: URLSessionDataTask?
    /// Requests made (tests).
    private(set) var requestCount = 0

    private init(url: URL, size: Int64, head: Data, session: URLSession) {
        self.url = url
        self.size = size
        self.session = session
        if !head.isEmpty { store(head, from: 0) }
    }

    /// Fetches the first block, which also tells the file's length (`Content-Range`).
    static func open(_ url: URL, session: URLSession = .shared) async throws -> HTTPRangeSource {
        var request = URLRequest(url: url)
        request.setValue("bytes=0-\(blockSize - 1)", forHTTPHeaderField: "Range")
        let (data, response) = try await session.data(for: request)
        let http = response as? HTTPURLResponse
        let size: Int64
        switch http?.statusCode {
        case 206:
            guard let total = http?.value(forHTTPHeaderField: "Content-Range").flatMap(Self.totalLength) else {
                throw Failure.noLength
            }
            size = total
        case 200:
            size = Int64(data.count)  // the server sent the whole (small) file
        default:
            throw Failure.status(http?.statusCode ?? 0)
        }
        let source = HTTPRangeSource(url: url, size: size, head: data, session: session)
        source.requestCount = 1
        return source
    }

    /// "bytes 0-65535/1234567" → 1234567.
    static func totalLength(_ contentRange: String) -> Int64? {
        contentRange.split(separator: "/").last.flatMap { Int64($0.trimmingCharacters(in: .whitespaces)) }
    }

    /// Stops reads in flight and refuses new ones (the item stopped playing).
    func cancel() {
        lock.withLock {
            cancelled = true
            task?.cancel()
        }
    }

    /// Up to `count` bytes at `offset` (fewer at the end of the file).
    func read(at offset: Int64, count: Int) throws -> Data {
        guard offset >= 0, offset < size, count > 0 else { return Data() }
        let end = min(offset + Int64(count), size)
        var result = Data(capacity: Int(end - offset))
        var position = offset
        while position < end {
            let index = position / Int64(Self.blockSize)
            let block = try self.block(index)
            let start = Int(position - index * Int64(Self.blockSize))
            let take = min(block.count - start, Int(end - position))
            guard take > 0 else { break }
            result.append(block[block.startIndex + start..<block.startIndex + start + take])
            position += Int64(take)
        }
        return result
    }

    private func block(_ index: Int64) throws -> Data {
        let (cached, sequential): (Data?, Bool) = lock.withLock {
            if cancelled { return (nil, false) }
            let hit = blocks[index]
            let sequential = index == lastBlock + 1
            lastBlock = index
            if hit != nil { touch(index) }
            return (hit, sequential)
        }
        if let cached { return cached }
        try lock.withLock { if cancelled { throw Failure.cancelled } }
        let blockCount = Int64(sequential ? Self.sequentialBlocks : 1)
        let from = index * Int64(Self.blockSize)
        let to = min(from + blockCount * Int64(Self.blockSize), size) - 1
        let data = try fetch(from: from, to: to)
        lock.withLock { store(data, from: from) }
        return lock.withLock { blocks[index] } ?? Data()
    }

    /// One Range request, waited for on this thread.
    private func fetch(from: Int64, to: Int64) throws -> Data {
        var request = URLRequest(url: url)
        request.setValue("bytes=\(from)-\(to)", forHTTPHeaderField: "Range")
        let box = ResultBox()
        let semaphore = DispatchSemaphore(value: 0)
        let task = session.dataTask(with: request) { data, response, error in
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if let error {
                box.result = .failure(error)
            } else if status == 206, let data {
                box.result = .success(data)
            } else if status == 200, let data, from == 0 {
                box.result = .success(data.prefix(Int(to - from + 1)))
            } else {
                box.result = .failure(Failure.status(status))
            }
            semaphore.signal()
        }
        // A cancel that came in before the request started must not let it run.
        let alreadyCancelled = lock.withLock {
            requestCount += 1
            self.task = task
            return cancelled
        }
        if alreadyCancelled { throw Failure.cancelled }
        task.resume()
        // URLSession has its own timeouts; this one only keeps a reader thread from
        // waiting for ever on an answer that never comes.
        if semaphore.wait(timeout: .now() + Self.readTimeout) == .timedOut {
            task.cancel()
            throw Failure.timedOut
        }
        if lock.withLock({ cancelled }) { throw Failure.cancelled }
        return try box.result!.get()
    }

    /// Splits `data` (starting at a block boundary) into blocks; evicts the oldest.
    private func store(_ data: Data, from offset: Int64) {
        var index = offset / Int64(Self.blockSize)
        var start = data.startIndex
        while start < data.endIndex {
            let end = min(start + Self.blockSize, data.endIndex)
            blocks[index] = Data(data[start..<end])
            touch(index)
            index += 1
            start = end
        }
        while recent.count > Self.cacheLimit {
            blocks[recent.removeFirst()] = nil
        }
    }

    private func touch(_ index: Int64) {
        if let at = recent.lastIndex(of: index) { recent.remove(at: at) }
        recent.append(index)
    }
}

private nonisolated final class ResultBox: @unchecked Sendable {
    var result: Result<Data, any Error>?
}

/// The containers the app tells apart, from the file's media type (`PlaybackRead.mediaType`,
/// the stream's `Content-Type`): Ogg and Matroska/WebM go to `DecodingEngine`, everything
/// else to AVPlayer.
nonisolated enum AudioContainer: Equatable {
    case ogg, matroska, other

    init(mediaType: String) {
        let essence = mediaType.split(separator: ";").first.map {
            $0.trimmingCharacters(in: .whitespaces).lowercased()
        }
        switch essence {
        case "audio/ogg", "application/ogg": self = .ogg
        case "audio/webm", "video/webm", "audio/x-matroska", "video/x-matroska": self = .matroska
        default: self = .other
        }
    }
}
