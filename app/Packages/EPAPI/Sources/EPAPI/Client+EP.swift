public import Foundation
public import OpenAPIRuntime
import OpenAPIURLSession

extension Client {
    /// The client for an EP server, configured for its conventions (server/README.md,
    /// "API conventions"): timestamps are RFC 3339 with fractional seconds, and
    /// `withExplicitNulls` can send a JSON `null` in a request body.
    ///
    /// - Parameters:
    ///   - serverURL: The server's base URL, e.g. `http://localhost:8000`.
    ///   - transport: The HTTP transport; tests pass a stub.
    public static func ep(serverURL: URL, transport: any ClientTransport = URLSessionTransport()) -> Client {
        Client(
            serverURL: serverURL,
            configuration: Configuration(dateTranscoder: .iso8601WithFractionalSeconds),
            transport: transport,
            middlewares: [ExplicitNullsMiddleware()]
        )
    }
}
