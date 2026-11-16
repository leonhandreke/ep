import Foundation
import Testing

@testable import EP

struct AppSettingsTests {
    private func scratchDefaults() -> UserDefaults {
        let name = "EPTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        return defaults
    }

    @Test func defaultsToLocalhostAndPersistsChanges() {
        let defaults = scratchDefaults()
        let settings = AppSettings(defaults: defaults, environment: [:])
        #expect(settings.serverURL.absoluteString == "http://localhost:8000")
        #expect(!settings.isServerURLOverridden)

        settings.serverURL = URL(string: "http://ep.local:7710")!
        #expect(AppSettings(defaults: defaults, environment: [:]).serverURL.absoluteString == "http://ep.local:7710")
    }

    @Test func environmentOverridesWithoutPersisting() {
        let defaults = scratchDefaults()
        let settings = AppSettings(defaults: defaults, environment: ["EP_SERVER_URL": "http://127.0.0.1:18000"])
        #expect(settings.serverURL.absoluteString == "http://127.0.0.1:18000")
        #expect(settings.isServerURLOverridden)

        settings.serverURL = URL(string: "http://elsewhere:1")!
        #expect(defaults.string(forKey: AppSettings.serverURLKey) == nil)
    }

    @Test func parsesTypedAddresses() {
        #expect(AppSettings.parseServerURL("ep.local:7710")?.absoluteString == "http://ep.local:7710")
        #expect(AppSettings.parseServerURL(" https://ep.example/ ")?.absoluteString == "https://ep.example")
        #expect(AppSettings.parseServerURL("ftp://ep.local") == nil)
        #expect(AppSettings.parseServerURL("") == nil)
    }
}

struct ColumnGridTests {
    @Test func sharesWidthByFractionAboveTheMinimums() {
        #expect(ColumnGrid.widths(total: 2000, spec: .library) == [500, 600, 900])
    }

    @Test func holdsColumnsAtTheirMinimumAndSharesTheRest() {
        // 1200 by 25/30/45 would give the first column 300 and the last 540; at 1150 the
        // first share (287.5) is below 300, so it is held there and 850 is shared 30:45.
        #expect(ColumnGrid.widths(total: 1150, spec: .library).map { $0.rounded() } == [300, 340, 510])
        #expect(ColumnGrid.widths(total: 900, spec: .library) == [300, 340, 460])
    }
}
