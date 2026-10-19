import XCTest

/// The skeleton's launch paths against the full stub-model backend in fake-issuer mode
/// (iOS TDD §16.3, D22), in the UITest configuration: with no token the app shows sign-in;
/// with a pre-signed learner token it passes the real session probe (real bearer
/// validation against the fake issuer's JWKS) and shows an empty Your paths. W35 (AL-604)
/// builds on this with the paths list. Assertions are structural, never copy.
@MainActor
final class LaunchUITests: XCTestCase {
    func testLaunchesToSignInWithNoToken() {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()

        XCTAssertTrue(app.buttons["sign-in-button"].waitForExistence(timeout: 20))
        XCTAssertFalse(app.staticTexts["home-greeting"].exists)
        UITestScreenshots.capture(named: "uitest-sign-in", in: self)
    }

    func testLaunchesToYourPathsWithAUITestToken() throws {
        continueAfterFailure = false
        let tokens = try TestTokens.launchValue(.learner)
        let app = XCUIApplication()
        app.launchArguments += ["-AlephTestTokens", tokens]
        app.launch()

        XCTAssertTrue(app.staticTexts["home-greeting"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.buttons["account-menu"].exists)
        XCTAssertFalse(app.buttons["sign-in-button"].exists)
        UITestScreenshots.capture(named: "uitest-your-paths", in: self)
    }
}
