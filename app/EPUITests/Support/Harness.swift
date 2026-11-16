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

/// IDs from the seeded demo library (`ep.seed`: deterministic IDs).
enum Seed {
    static let bjork = "c100693e-b568-5ae6-9048-cada7a052a49"
    static let mum = "eede71ed-2e3c-58d6-9d0f-939c663768bf"
    static let fennesz = "4b85c2f2-4f70-5980-8394-00da568b1312"
    static let glennCopeland = "4a547d21-9b55-5eae-b141-eba121acca0f"
    static let homogenic = "b05eeaa0-9ef9-5e93-9bd4-72e74a3f73f2"
    static let vespertine = "4097e792-0ffd-542d-a5d9-0149722906ca"
    static let agora = "98bbb7ab-bd36-5f60-9235-b15196648d4f"
    static let medulla = "b42535c7-89ba-515c-b549-2885847c1b2f"
    /// Homogenic's tracks: 1 Hunter (own file), 2 Jóga (own file), 4 Bachelorette (only
    /// a Bandcamp link).
    static let hunterTrack = "21da786f-467c-521c-8860-b4d8ed89225a"
    static let jogaTrack = "074901fa-ee92-5673-84b9-824423f92176"
    static let bacheloretteTrack = "329a58d2-9ccb-5552-98bb-f556699b1fa2"
    /// Vespertine's Cocoon: plays only as an excerpt of the "Strings only" episode
    /// (estimated times).
    static let cocoonTrack = "3d5b5b0c-d1fa-5b06-8689-aa21bd66c13c"
    /// Medúlla's track 2: a range of the album's one file.
    static let medullaTrack2 = "cc168657-7f75-57d8-bf13-4b7e252f54a3"
    /// Sigur Rós: "Svefn-g-englar" on Ágætis byrjun (Ogg Vorbis), and Björk's loose
    /// "Loose Tone" (WebM/Opus).
    static let sigurRos = "f153cd55-29ee-525d-8193-0ce740fcebf9"
    static let agaetisByrjun = "ffffebf0-10df-5bfd-88ee-bdbe79721add"
    static let svefnTrack = "127d6f5e-1eb8-59d3-9424-365b62a4b069"
    static let looseTone = "68639e66-9269-5435-92c9-fbf4fa9ab941"
}
