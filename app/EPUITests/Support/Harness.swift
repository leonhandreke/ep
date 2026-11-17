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

    /// `PUT <path>` with a JSON body on the private backend (to change something behind
    /// the app's back, e.g. a note edited elsewhere).
    @discardableResult
    static func put(_ path: String, json: [String: Any]) async throws -> Any {
        try await send("PUT", path, json: json)
    }

    /// `PATCH <path>` with a JSON body (e.g. to put back what a test changed).
    @discardableResult
    static func patch(_ path: String, json: [String: Any]) async throws -> Any {
        try await send("PATCH", path, json: json)
    }

    private static func send(_ method: String, _ path: String, json: [String: Any]) async throws -> Any {
        var request = URLRequest(url: try requireAPI().appending(path: path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            throw HarnessError("\(method) \(path): HTTP \(status): \(String(decoding: data, as: UTF8.self))")
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
    /// Homogenic's 3 Unravel (file missing) and 5 All Is Full of Love; notes and tags are
    /// their recordings'. Bachelorette's recording. None has a note in the seed.
    static let unravelTrack = "362415ee-abfc-51ff-a4b6-2ba37bf2e156"
    static let unravelRecording = "bd61c959-25d3-549f-8510-c06b8a854217"
    static let allIsFullTrack = "25fc64dd-f770-55ad-934c-0e1efeb94884"
    static let bacheloretteRecording = "db5c57ea-ca9d-577d-9d7c-65d029b110ea"
    /// Mark Bell: linked from Björk's and Homogenic's notes; his own note has headings.
    static let markBell = "c43c42f9-3537-5b6b-afe3-a72893f0ce0d"
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
    /// The radio show "Late Night Tapes" and its Ogg Opus episode "Homage to the night
    /// shift" (60 s): entries 1 Jóga 0:05–0:15, 2 Loose Tone 0:15–0:25, 3 Ever New
    /// (radio only), 4 Hour Logic (radio only), 5 not matched.
    static let lateNightTapes = "18535ba5-749e-518e-bcef-5a27754f28a5"
    static let nightShift = "4e3839f0-c397-555a-abbb-202f0d909084"
    static let nightShiftEntry1 = "0fecbb61-cf41-5537-852e-f8963eb84e43"
    static let nightShiftEntry2 = "a4eb9251-3de0-5ef4-bfb0-ef05e1c85668"
    /// The tag "strings" (Homogenic, Jóga, Vespertine).
    static let stringsTag = "29734eee-8a42-579d-9c51-07f68f4f9b80"
    /// Import jobs, one per state: Agora running (downloading 3 of 5), Bunny Rabbit queued
    /// behind it, Shade failed (an error), Neighborhoods in review with two failed
    /// downloads, Ágætis byrjun in review (another release of an album in the library),
    /// the NTS episode "Turiya and friends" in review (same recording?, no title, which
    /// Arca?, one answered), Treasure and Untrue done.
    static let agoraJob = "94087dcb-e486-5f50-8538-230a41897245"
    static let bunnyJob = "9f15ee56-e29c-5ee5-8551-2c6d92b31fae"
    static let shadeJob = "c9d118df-258a-5174-8e5d-5e649abd12e8"
    static let playlistJob = "85d54eef-d0d8-5d78-b129-061a58c1d51a"
    static let agaetisJob = "756717e8-e3c7-57be-b8c9-a0b3b312f141"
    static let turiyaJob = "340733ea-43dc-565c-a805-85e8af2c8158"
    static let treasureJob = "2c8b134e-fe16-561c-8071-57973258facf"
    /// The episode job's track keys are its URL with "#<entry>".
    static let turiyaURL = "https://www.nts.live/shows/late-night-tapes/episodes/turiya-and-friends"
}
