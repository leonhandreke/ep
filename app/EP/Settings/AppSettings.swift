import Foundation
import Observation

/// App-wide settings, stored in UserDefaults.
///
/// The server address can be overridden for one run, by the environment variable
/// `EP_SERVER_URL` or the launch argument `-serverURL <url>` (UserDefaults' argument
/// domain). An overridden address is never written back, so a run against a private
/// backend (the harness) leaves the user's settings alone.
@Observable
final class AppSettings {
    static let defaultServerURL = URL(string: "http://localhost:8000")!
    static let serverURLKey = "serverURL"
    static let serverURLEnvironmentVariable = "EP_SERVER_URL"

    /// The EP server's base URL.
    var serverURL: URL {
        didSet {
            guard !isServerURLOverridden else { return }
            defaults.set(serverURL.absoluteString, forKey: Self.serverURLKey)
        }
    }

    /// True when this run's address comes from the environment or a launch argument.
    let isServerURLOverridden: Bool

    @ObservationIgnored private let defaults: UserDefaults

    init(
        defaults: UserDefaults = .standard,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) {
        self.defaults = defaults
        let argument = defaults.volatileDomain(forName: UserDefaults.argumentDomain)[Self.serverURLKey] as? String
        let override = (environment[Self.serverURLEnvironmentVariable] ?? argument).flatMap(Self.parseServerURL)
        isServerURLOverridden = override != nil
        serverURL =
            override
            ?? defaults.string(forKey: Self.serverURLKey).flatMap(Self.parseServerURL)
            ?? Self.defaultServerURL
    }

    /// An `http(s)` URL with a host, from what the user typed; `nil` if it is not one.
    /// A missing scheme means `http` ("localhost:8000", "ep.local:7710").
    static func parseServerURL(_ text: String) -> URL? {
        var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.contains("://") { text = "http://" + text }
        while text.hasSuffix("/") { text.removeLast() }
        guard let components = URLComponents(string: text),
            let scheme = components.scheme?.lowercased(), ["http", "https"].contains(scheme),
            let host = components.host, !host.isEmpty
        else { return nil }
        return components.url
    }
}

extension URL {
    /// "host:port" as the UI shows a server address ("localhost:8000").
    var hostAndPort: String {
        guard let host = host() else { return absoluteString }
        return port.map { "\(host):\($0)" } ?? host
    }
}
