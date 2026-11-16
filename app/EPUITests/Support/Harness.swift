import XCTest

/// What `app/scripts/ui-test/uitest` hands the tests. xcodebuild passes environment
/// variables named `TEST_RUNNER_<name>` to the test runner as `<name>`.
enum Harness {
    private static let environment = ProcessInfo.processInfo.environment

    /// The private seeded backend (`uitest up`), e.g. http://127.0.0.1:18439.
    static let apiURL = environment["EP_UITEST_API"].flatMap(URL.init(string:))
    /// The harness's copy of EP.app: bundle id `de.ndreke.ep.uitest`, so its settings,
    /// window frames and saved state never touch `de.ndreke.ep(.debug)`.
    static let appURL = environment["EP_UITEST_APP"].map { URL(fileURLWithPath: $0) }
    /// The appearance every launch uses: light (for now the harness runs light only;
    /// `uitest shot --dark` exists for a one-off look).
    static let appearance = environment["EP_UITEST_APPEARANCE"].flatMap(Appearance.init(rawValue:)) ?? .light
    /// The main window's size, "1280x800" (`uitest shot --size`); the app's default otherwise.
    static let windowSize = environment["EP_UITEST_SIZE"]
    /// An address where nothing listens (the discard port), for the unreachable state.
    static let unreachableURL = URL(string: "http://127.0.0.1:9")!

    static var isActive: Bool { apiURL != nil && appURL != nil }

    /// The backend's address; skips the test when it was not started through `uitest`.
    static func requireAPI() throws -> URL {
        guard let apiURL, isActive else {
            throw XCTSkip("Needs the harness backend: run app/scripts/ui-test/uitest test")
        }
        return apiURL
    }

    /// `GET <path>` on the private backend, decoded as JSON (for assertions the UI alone
    /// cannot show, e.g. that an edit reached the server).
    static func get(_ path: String) async throws -> Any {
        let url = try requireAPI().appending(path: path)
        let (data, response) = try await URLSession.shared.data(from: url)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            throw HarnessError("GET \(path): HTTP \(status): \(String(decoding: data, as: UTF8.self))")
        }
        return try JSONSerialization.jsonObject(with: data)
    }
}

enum Appearance: String {
    case light, dark
}

/// Which server a launch talks to.
enum Server {
    /// The private backend, set for this run (`EP_SERVER_URL`).
    case harness
    /// An address where nothing listens.
    case unreachable
    /// Whatever the app's settings say; `uitest` saves the private backend there before
    /// every run. Only for tests of the setting itself: an overridden address cannot be
    /// edited in Settings.
    case saved
}

struct HarnessError: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}
