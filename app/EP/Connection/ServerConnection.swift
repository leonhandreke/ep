import EPAPI
import Foundation
import Observation
import OpenAPIRuntime
import OpenAPIURLSession

/// The app-wide API client and whether the server answers.
///
/// Stores get the client from here (`connection.client`). `connect()` asks the server
/// for `GET /server`; a failure makes the state `.unreachable` until the next attempt.
@Observable
final class ServerConnection {
    enum State {
        case connecting
        case connected(Components.Schemas.ServerRead)
        case unreachable(message: String)
    }

    private(set) var state: State = .connecting
    private(set) var serverURL: URL
    private(set) var client: Client

    @ObservationIgnored private let transport: any ClientTransport
    /// Counts attempts, so a slow answer to an earlier one never overwrites a later one.
    @ObservationIgnored private var attempt = 0

    init(serverURL: URL, transport: any ClientTransport = URLSessionTransport()) {
        self.serverURL = serverURL
        self.transport = transport
        client = .ep(serverURL: serverURL, transport: transport)
    }

    var isConnected: Bool {
        if case .connected = state { true } else { false }
    }

    /// Points the client at another server and connects to it.
    func use(serverURL: URL) async {
        guard serverURL != self.serverURL else { return await connect() }
        self.serverURL = serverURL
        client = .ep(serverURL: serverURL, transport: transport)
        await connect()
    }

    /// Asks the server who it is; on demand (Retry) and whenever the address changes.
    func connect() async {
        attempt += 1
        let current = attempt
        state = .connecting
        let result: State
        do {
            let server = try await client.getServer().ok.body.json
            result = .connected(server)
        } catch {
            result = .unreachable(message: Self.describe(error))
        }
        guard current == attempt else { return }
        state = result
    }

    /// Asks the server again without passing through `.connecting` (window activation):
    /// what is shown stays while the answer is on its way.
    func refresh() async {
        guard isConnected else { return await connect() }
        attempt += 1
        let current = attempt
        let result: State
        do {
            result = .connected(try await client.getServer().ok.body.json)
        } catch {
            guard !Self.isCancellation(error) else { return }
            result = .unreachable(message: Self.describe(error))
        }
        guard current == attempt else { return }
        state = result
    }

    /// A store's request failed. A transport failure (no answer at all) means the server
    /// is unreachable: the banner appears, and what is loaded stays visible. Errors the
    /// server answered with (404, 422, …) are the store's business.
    func reportFailure(_ error: any Error) {
        guard isConnected, Self.isTransportFailure(error) else { return }
        attempt += 1
        state = .unreachable(message: Self.describe(error))
    }

    nonisolated static func isCancellation(_ error: any Error) -> Bool {
        let underlying = (error as? ClientError)?.underlyingError ?? error
        return underlying is CancellationError || (underlying as? URLError)?.code == .cancelled
    }

    nonisolated static func isTransportFailure(_ error: any Error) -> Bool {
        let underlying = (error as? ClientError)?.underlyingError ?? error
        guard let urlError = underlying as? URLError else { return false }
        return urlError.code != .cancelled
    }

    private static func describe(_ error: any Error) -> String {
        let underlying = (error as? ClientError)?.underlyingError ?? error
        if let urlError = underlying as? URLError {
            return urlError.localizedDescription
        }
        return String(describing: underlying)
    }
}
