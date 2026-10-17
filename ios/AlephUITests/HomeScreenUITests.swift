import XCTest

/// Launches the real app on the simulator and captures the home screen.
///
/// Each screenshot is kept as an XCTest attachment (exportable from the
/// .xcresult) and, when `SCREENSHOT_DIR` is set (CI passes
/// `TEST_RUNNER_SCREENSHOT_DIR`), also written there as a PNG.
final class HomeScreenUITests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    func testHomeScreenLight() { capture(scheme: "light") }
    func testHomeScreenDark() { capture(scheme: "dark") }

    private func capture(scheme: String) {
        let app = XCUIApplication()
        app.launchEnvironment["ALEPH_COLOR_SCHEME"] = scheme
        app.launch()

        XCTAssertTrue(app.staticTexts["your-paths-heading"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["new-path-button"].exists)
        XCTAssertTrue(app.navigationBars["aleph"].exists)

        let screenshot = XCUIScreen.main.screenshot()
        let name = "uitest-home-\(scheme)"
        let attachment = XCTAttachment(screenshot: screenshot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)

        if let dir = ProcessInfo.processInfo.environment["SCREENSHOT_DIR"], !dir.isEmpty {
            let url = URL(fileURLWithPath: dir).appendingPathComponent("\(name).png")
            try? FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            do {
                try screenshot.pngRepresentation.write(to: url)
            } catch {
                XCTFail("could not write \(url.path): \(error)")
            }
        }
    }
}
