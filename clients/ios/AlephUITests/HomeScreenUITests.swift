import XCTest

/// Launches the real app on the simulator and captures the home screen.
///
/// The screenshot is never compared (it includes the live status bar; CI
/// overrides the clock to 9:41 to keep commits stable, but treats the image as
/// a review aid only).
///
/// Each screenshot is kept as an XCTest attachment (exportable from the
/// .xcresult) and, when `SCREENSHOT_DIR` is set (CI passes
/// `TEST_RUNNER_SCREENSHOT_DIR`), also written there as a PNG.
final class HomeScreenUITests: XCTestCase {
    override func setUp() {
        continueAfterFailure = false
    }

    func testHomeScreen() {
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.staticTexts["your-paths-heading"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["new-path-button"].exists)
        XCTAssertTrue(app.navigationBars["aleph"].exists)

        let screenshot = XCUIScreen.main.screenshot()
        let name = "uitest-home-dark"
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
