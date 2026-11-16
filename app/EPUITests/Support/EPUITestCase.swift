import XCTest

/// Base class for harness tests: launches the app against the private backend, finds
/// controls by accessibility identifier, waits, and takes screenshots. On a failure the
/// app's window is attached as `failure` (the `uitest` script exports it).
@MainActor
class EPUITestCase: XCTestCase {
    /// The app this test launched last (terminated in tearDown).
    private(set) var app: XCUIApplication?

    nonisolated override func setUpWithError() throws {
        continueAfterFailure = false
        _ = try Harness.requireAPI()
    }

    nonisolated override func tearDown() {
        let failed = (testRun?.totalFailureCount ?? 0) > 0
        // XCTest calls the synchronous tearDown on the main thread, where the test ran.
        nonisolated(unsafe) let test = self
        MainActor.assumeIsolated { test.finish(failed: failed) }
        super.tearDown()
    }

    private func finish(failed: Bool) {
        if failed, let app, app.state == .runningForeground || app.state == .runningBackground {
            attachScreenshot(named: "failure", of: app.windows.firstMatch, in: app)
        }
        app?.terminate()
    }

    // MARK: - Launching

    /// Launches the harness copy of EP and waits for its main window. Each launch starts
    /// with no restored windows; settings are those `uitest` wrote before the run.
    @discardableResult
    func launch(server: Server = .harness, appearance: Appearance = Harness.appearance) throws -> XCUIApplication {
        app?.terminate()
        guard let url = Harness.appURL else { throw XCTSkip("Run through app/scripts/ui-test/uitest") }
        let app = XCUIApplication(url: url)
        switch server {
        case .harness: app.launchEnvironment["EP_SERVER_URL"] = try Harness.requireAPI().absoluteString
        case .unreachable: app.launchEnvironment["EP_SERVER_URL"] = Harness.unreachableURL.absoluteString
        case .saved: break
        }
        app.launchArguments += [
            "-ApplePersistenceIgnoreState", "YES",
            "-appearance", appearance.rawValue,
            "-NSAutomaticWindowAnimationsEnabled", "NO",
        ]
        if let size = Harness.windowSize { app.launchArguments += ["-windowSize", size] }
        app.launch()
        self.app = app
        XCTAssertTrue(
            app.windows.firstMatch.exists || app.windows.firstMatch.waitForExistence(timeout: 10), "EP opened no window"
        )
        return app
    }

    /// Launches against the private backend and waits until the main window shows it
    /// is connected.
    @discardableResult
    func launchConnected(appearance: Appearance = Harness.appearance) throws -> XCUIApplication {
        let app = try launch(server: .harness, appearance: appearance)
        waitForText("Connected to", in: app.element("connection.status"))
        return app
    }

    // MARK: - Finding and waiting

    /// Waits up to `timeout` seconds until `element` exists; fails the test otherwise.
    /// (Checks first: `waitForExistence` alone takes about a second even for an element
    /// that is already there.)
    @discardableResult
    func waitFor(
        _ element: XCUIElement, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line
    ) -> XCUIElement {
        XCTAssertTrue(
            element.exists || element.waitForExistence(timeout: timeout), "\(element.description) did not appear",
            file: file,
            line: line)
        return element
    }

    /// Waits until `element` is gone.
    func waitForAbsence(
        _ element: XCUIElement, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line
    ) {
        XCTAssertTrue(
            !element.exists || element.waitForNonExistence(timeout: timeout), "\(element.description) did not go away",
            file: file,
            line: line)
    }

    /// Waits until `element`'s label or value contains `text` (macOS puts a text's
    /// string in its value, a combined element's in its label).
    func waitForText(
        _ text: String, in element: XCUIElement, timeout: TimeInterval = 10, file: StaticString = #filePath,
        line: UInt = #line
    ) {
        waitFor(element, timeout: timeout, file: file, line: line)
        let deadline = Date.now.addingTimeInterval(timeout)
        while !element.text.contains(text), Date.now < deadline {
            RunLoop.current.run(until: .now.addingTimeInterval(0.1))
        }
        XCTAssertTrue(
            element.text.contains(text), "\(element.identifier) shows “\(element.text)”, not “\(text)”", file: file,
            line: line)
    }

    // MARK: - Screenshots

    /// Brings the app to the front and attaches a PNG of `element` (default: the frontmost
    /// window) named `name`. `uitest shot` exports these as `<name>.png`.
    func screenshot(_ name: String, of element: XCUIElement? = nil) {
        guard let app else { return XCTFail("screenshot before launch") }
        attachScreenshot(named: name, of: element ?? app.windows.firstMatch, in: app)
    }

    private func attachScreenshot(named name: String, of element: XCUIElement, in app: XCUIApplication) {
        // A capture shows what is on screen, so make sure no other app's window covers ours.
        app.activate()
        let attachment = XCTAttachment(screenshot: element.screenshot())
        // "<name>@<w>x<h>": the size in points, so `uitest` can scale Retina pixels to points.
        attachment.name = "\(name)@\(Int(element.frame.width))x\(Int(element.frame.height))"
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

extension XCUIElement {
    /// The descendant with this accessibility identifier (`toolbar.section`, …), whatever
    /// its type.
    func element(_ identifier: String) -> XCUIElement {
        descendants(matching: .any).matching(identifier: identifier).firstMatch
    }

    /// The window that contains the element with this identifier.
    func window(containing identifier: String) -> XCUIElement {
        windows.containing(.any, identifier: identifier).firstMatch
    }

    /// Label and value, as one string to search.
    var text: String {
        [label, value as? String ?? ""].filter { !$0.isEmpty }.joined(separator: " ")
    }

    /// Selects everything in a text field and types `text` followed by Return.
    func replaceText(with text: String) {
        click()
        typeKey("a", modifierFlags: .command)
        typeText(text + "\n")
    }
}
