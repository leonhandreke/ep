import EPAPI
import Foundation
import HTTPTypes
import OpenAPIRuntime

/// A transport that records requests and answers from a closure.
nonisolated final class StubTransport: ClientTransport, @unchecked Sendable {
    struct Request {
        var request: HTTPRequest
        var body: Data?
    }

    private let lock = NSLock()
    private var recorded: [Request] = []
    private let respond: @Sendable (HTTPRequest) throws -> (HTTPResponse, Data?)

    init(respond: @escaping @Sendable (HTTPRequest) throws -> (HTTPResponse, Data?)) {
        self.respond = respond
    }

    /// Answers every request with `status` and a JSON body.
    convenience init(status: HTTPResponse.Status = .ok, json: String) {
        self.init { _ in
            (HTTPResponse(status: status, headerFields: [.contentType: "application/json"]), Data(json.utf8))
        }
    }

    var requests: [Request] { lock.withLock { recorded } }

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let data: Data? = if let body { try await Data(collecting: body, upTo: 1 << 20) } else { nil }
        lock.withLock { recorded.append(Request(request: request, body: data)) }
        let (response, responseBody) = try respond(request)
        return (response, responseBody.map { HTTPBody($0) })
    }
}

nonisolated enum Fixtures {
    static let server = """
        {
          "version": "0.1.0",
          "counts": {"artists": 12, "radio_only_artists": 3, "release_groups": 9, "recordings": 40,
                     "tracks": 38, "radio_shows": 1, "radio_episodes": 2},
          "library_dir": {"path": "/library", "host_path": null},
          "media_roots": []
        }
        """
}
