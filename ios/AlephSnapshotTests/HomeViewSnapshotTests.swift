import SnapshotTesting
import SwiftUI
import XCTest

@testable import Aleph

/// Snapshot of the home screen, light and dark, at iPhone 13 size (390x844).
///
/// Set `SNAPSHOT_DIR` (CI passes `TEST_RUNNER_SNAPSHOT_DIR`) to write the
/// references somewhere other than `__Snapshots__/` next to this file.
/// Set `SNAPSHOT_RECORD=1` to (re)record: recording never fails the test, so
/// the first CI run with no references on disk stays green.
final class HomeViewSnapshotTests: XCTestCase {
    private var env: [String: String] { ProcessInfo.processInfo.environment }
    private var recording: Bool { env["SNAPSHOT_RECORD"] == "1" }

    func testHomeLight() { assertHome(style: .light, name: "home-light") }
    func testHomeDark() { assertHome(style: .dark, name: "home-dark") }

    private func assertHome(
        style: UIUserInterfaceStyle, name: String,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        let view = HomeView()
            .preferredColorScheme(style == .dark ? .dark : .light)
        let failure = verifySnapshot(
            of: view,
            as: .image(
                layout: .device(config: .iPhone13),
                traits: UITraitCollection(userInterfaceStyle: style)
            ),
            named: name,
            record: recording,
            snapshotDirectory: env["SNAPSHOT_DIR"],
            file: file,
            testName: "HomeView",
            line: line
        )
        if let failure, !recording {
            XCTFail(failure, file: file, line: line)
        }
    }
}
