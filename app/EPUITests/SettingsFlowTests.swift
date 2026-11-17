import XCTest

/// Watched folders in Settings change the server's: pausing one and watching it again.
final class SettingsFlowTests: EPUITestCase {
    func testPausingAWatchedFolderReachesTheServer() async throws {
        let folders = try await Harness.get("watch-dirs") as? [[String: Any]]
        let id = try XCTUnwrap(folders?.first?["id"] as? String, "the seed has no watched folder")
        let app = try launchConnected()
        app.typeKey(",", modifierFlags: .command)
        let settings = app.window(containing: "settings.serverURL")
        let checkbox = waitFor(settings.element("settings.folder.\(id).enabled"))
        let wasEnabled = try await Self.enabled(id)

        checkbox.click()
        let changed = try await Self.wait(id, enabled: !wasEnabled)
        XCTAssertEqual(changed, !wasEnabled)
        if wasEnabled {
            waitForText("paused", in: settings.element("settings.folder.\(id).status"))
        }

        // Put it back for the other tests.
        checkbox.click()
        let restored = try await Self.wait(id, enabled: wasEnabled)
        XCTAssertEqual(restored, wasEnabled)
    }

    /// The folder's `enabled` once it is `enabled` (or after 10 s).
    private static func wait(_ id: String, enabled: Bool) async throws -> Bool {
        let deadline = Date.now.addingTimeInterval(10)
        var value = try await Self.enabled(id)
        while value != enabled, Date.now < deadline {
            try await Task.sleep(for: .milliseconds(200))
            value = try await Self.enabled(id)
        }
        return value
    }

    private static func enabled(_ id: String) async throws -> Bool {
        let folder = try await Harness.get("watch-dirs/\(id)") as? [String: Any]
        return try XCTUnwrap(folder?["enabled"] as? Bool)
    }
}
