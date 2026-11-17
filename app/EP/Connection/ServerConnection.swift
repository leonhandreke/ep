import EPAPI
import Foundation
import Observation
import OpenAPIRuntime
import OpenAPIURLSession

/// The app-wide API client and whether the server answers.
///
/// Stores get the client from here (`connection.client`). `connect()` asks the server
/// for `GET /server`; a failure makes the state `.unreachable` until the next attempt.
/// While unreachable it tries again by itself (decision 12, §11.2), after 5, 10, 20, then
/// every 30 seconds; `retryAt` says when, for the banner's countdown.
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
    /// When the next automatic attempt starts, while unreachable.
    private(set) var retryAt: Date?

    /// Seconds between automatic attempts: the n-th failure in a row waits `retryDelays[n]`
    /// (the last one again after that). Empty: no automatic attempts (unit tests).
    @ObservationIgnored let retryDelays: [Duration]
    /// Failed attempts in a row (0 once connected).
    private(set) var failures = 0
    @ObservationIgnored private var retryTask: Task<Void, Never>?

    @ObservationIgnored private let transport: any ClientTransport
    /// Counts attempts, so a slow answer to an earlier one never overwrites a later one.
    @ObservationIgnored private var attempt = 0

    init(
        serverURL: URL, transport: any ClientTransport = URLSessionTransport(),
        retryDelays: [Duration] = [.seconds(5), .seconds(10), .seconds(20), .seconds(30)]
    ) {
        self.serverURL = serverURL
        self.transport = transport
        self.retryDelays = retryDelays
        client = .ep(serverURL: serverURL, transport: transport)
    }

    var isConnected: Bool {
        if case .connected = state { true } else { false }
    }

    /// Unreachable, or trying again after a failure: the window shows the banner and
    /// dims what it last loaded.
    var isOffline: Bool { !isConnected && failures > 0 }

    /// Points the client at another server and connects to it.
    func use(serverURL: URL) async {
        guard serverURL != self.serverURL else { return await connect() }
        self.serverURL = serverURL
        client = .ep(serverURL: serverURL, transport: transport)
        failures = 0
        await connect()
    }

    /// Asks the server who it is; on demand (Retry) and whenever the address changes.
    func connect() async {
        attempt += 1
        let current = attempt
        cancelRetry()
        state = .connecting
        let result: State
        do {
            let server = try await client.getServer().ok.body.json
            result = .connected(server)
        } catch {
            result = .unreachable(message: Self.describe(error))
        }
        guard current == attempt else { return }
        settle(result)
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
        settle(result)
    }

    /// A store's request failed. A transport failure (no answer at all) means the server
    /// is unreachable: the banner appears, and what is loaded stays visible. Errors the
    /// server answered with (404, 422, …) are the store's business.
    func reportFailure(_ error: any Error) {
        guard isConnected, Self.isTransportFailure(error) else { return }
        attempt += 1
        settle(.unreachable(message: Self.describe(error)))
    }

    // MARK: - Automatic retry

    /// The delay before the next automatic attempt after `failures` failures in a row.
    nonisolated static func retryDelay(afterFailures failures: Int, delays: [Duration]) -> Duration? {
        guard failures > 0, let last = delays.last else { return nil }
        return failures <= delays.count ? delays[failures - 1] : last
    }

    private func settle(_ result: State) {
        state = result
        if case .unreachable = result {
            failures += 1
            scheduleRetry()
        } else {
            failures = 0
            cancelRetry()
        }
    }

    private func scheduleRetry() {
        cancelRetry()
        guard let delay = Self.retryDelay(afterFailures: failures, delays: retryDelays) else { return }
        retryAt = .now.addingTimeInterval(TimeInterval(delay.components.seconds))
        retryTask = Task { [weak self] in
            do { try await Task.sleep(for: delay) } catch { return }
            guard let self else { return }
            // `connect()` cancels a scheduled retry: this one is running, not scheduled.
            retryTask = nil
            await connect()
        }
    }

    private func cancelRetry() {
        retryTask?.cancel()
        retryTask = nil
        retryAt = nil
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
