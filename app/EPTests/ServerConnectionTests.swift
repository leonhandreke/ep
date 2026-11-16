import EPAPI
import Foundation
import HTTPTypes
import Synchronization
import Testing

@testable import EP

struct ServerConnectionTests {
    @Test func connectsWhenTheServerAnswers() async throws {
        let transport = StubTransport(json: Fixtures.server)
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test:8000")!, transport: transport)

        await connection.connect()

        guard case .connected(let server) = connection.state else {
            Issue.record("expected connected, got \(connection.state)")
            return
        }
        #expect(server.version == "0.1.0")
        #expect(server.counts.artists == 12)
        #expect(transport.requests.first?.request.path == "/server")
    }

    @Test func isUnreachableWhenTheRequestFailsAndRecoversOnRetry() async throws {
        let up = Mutex(false)
        let transport = StubTransport { _ in
            guard up.withLock({ $0 }) else { throw URLError(.cannotConnectToHost) }
            return (
                HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]), Data(Fixtures.server.utf8)
            )
        }
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test:8000")!, transport: transport)

        await connection.connect()
        #expect(!connection.isConnected)
        guard case .unreachable = connection.state else {
            Issue.record("expected unreachable, got \(connection.state)")
            return
        }

        up.withLock { $0 = true }
        await connection.connect()
        #expect(connection.isConnected)
    }

    @Test func anUnexpectedStatusIsUnreachable() async {
        let transport = StubTransport(status: .internalServerError, json: #"{"detail": "boom"}"#)
        let connection = ServerConnection(serverURL: URL(string: "http://ep.test:8000")!, transport: transport)
        await connection.connect()
        #expect(!connection.isConnected)
    }
}
