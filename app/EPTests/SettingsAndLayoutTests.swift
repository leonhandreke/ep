import Foundation
import Testing

@testable import EP

struct AppSettingsTests {
    /// A defaults domain of the test's own, emptied before and after (cfprefsd keeps an
    /// empty file per domain, so the name is fixed rather than unique per run).
    private func withScratchDefaults(test: String = #function, _ body: (UserDefaults) -> Void) {
        let name = "de.ndreke.ep.debug.tests.\(test.prefix { $0 != "(" })"
        let defaults = UserDefaults(suiteName: name)!
        defaults.removePersistentDomain(forName: name)
        defer { defaults.removePersistentDomain(forName: name) }
        body(defaults)
    }

    @Test func defaultsToLocalhostAndPersistsChanges() {
        withScratchDefaults { defaults in
            let settings = AppSettings(defaults: defaults, environment: [:])
            #expect(settings.serverURL.absoluteString == "http://localhost:8000")
            #expect(!settings.isServerURLOverridden)

            settings.serverURL = URL(string: "http://ep.local:7710")!
            #expect(
                AppSettings(defaults: defaults, environment: [:]).serverURL.absoluteString == "http://ep.local:7710")
        }
    }

    @Test func environmentOverridesWithoutPersisting() {
        withScratchDefaults { defaults in
            let settings = AppSettings(defaults: defaults, environment: ["EP_SERVER_URL": "http://127.0.0.1:18000"])
            #expect(settings.serverURL.absoluteString == "http://127.0.0.1:18000")
            #expect(settings.isServerURLOverridden)

            settings.serverURL = URL(string: "http://elsewhere:1")!
            #expect(defaults.string(forKey: AppSettings.serverURLKey) == nil)
        }
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

    @Test func leavesTheGapsOutOfTheShares() {
        // Player bar: 1fr/2fr/1fr with 16 pt gaps.
        #expect(ColumnGrid.widths(total: 1232, spec: .playerBar) == [300, 600, 300])
        #expect(ColumnSpec.playerBar.minimumWidth == 932)
    }
}

struct LaunchOptionsTests {
    @Test func parsesLaunchWindowSizes() {
        #expect(LaunchOptions.parseSize("1280x800") == CGSize(width: 1280, height: 800))
        #expect(LaunchOptions.parseSize("1280X800") == CGSize(width: 1280, height: 800))
        #expect(LaunchOptions.parseSize("1280") == nil)
        #expect(LaunchOptions.parseSize("0x800") == nil)
        #expect(LaunchOptions.parseSize("wide") == nil)
    }
}
