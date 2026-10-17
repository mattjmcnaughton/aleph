import SnapshotTesting
import SwiftUI
import XCTest

@testable import Aleph

/// Snapshots of the home screen in dark mode (the app is dark only): default
/// text size at iPhone 13 size (390x844), and accessibility Dynamic Type
/// (AX3, `.accessibilityExtraLarge`) on a double-height canvas so the whole
/// screen is visible.
///
/// References live in `__Snapshots__/` beside this file and are committed;
/// `SNAPSHOT_DIR` overrides the location.
///
/// - Default (compare): a mismatch or a missing reference fails the test.
///   swift-snapshot-testing writes the failing image to `SNAPSHOT_ARTIFACTS`
///   (default: the temp dir) and attaches reference/failure/difference images
///   to the test result.
/// - `SNAPSHOT_RECORD=1`: (re)record every reference; recording never fails.
final class HomeViewSnapshotTests: XCTestCase {
    private var env: [String: String] { ProcessInfo.processInfo.environment }
    private var recording: Bool { env["SNAPSHOT_RECORD"] == "1" }

    func testHomeDark() {
        assertHome(
            name: "home-dark",
            size: .large,
            config: .iPhone13
        )
    }

    func testHomeDarkAccessibilityText() {
        let iPhone13 = ViewImageConfig.iPhone13
        assertHome(
            name: "home-dark-ax3",
            size: .accessibilityExtraLarge,
            config: ViewImageConfig(
                safeArea: iPhone13.safeArea,
                size: CGSize(width: 390, height: 1688),
                traits: iPhone13.traits
            )
        )
    }

    private func assertHome(
        name: String,
        size: UIContentSizeCategory,
        config: ViewImageConfig,
        file: StaticString = #filePath, line: UInt = #line
    ) {
        let view = HomeView()
            .preferredColorScheme(.dark)
            .dynamicTypeSize(DynamicTypeSize(size) ?? .large)
        let traits = UITraitCollection { traits in
            traits.userInterfaceStyle = .dark
            traits.preferredContentSizeCategory = size
        }
        let directory = env["SNAPSHOT_DIR"].flatMap { $0.isEmpty ? nil : $0 }
            ?? URL(fileURLWithPath: "\(file)")
            .deletingLastPathComponent()
            .appendingPathComponent("__Snapshots__")
            .path

        let failure = withSnapshotTesting(record: recording ? .all : .never) {
            verifySnapshot(
                of: view,
                // Tolerate sub-pixel anti-aliasing noise; real changes still fail.
                as: .image(
                    precision: 0.995,
                    perceptualPrecision: 0.98,
                    layout: .device(config: config),
                    traits: traits
                ),
                named: name,
                snapshotDirectory: directory,
                file: file,
                testName: "HomeView",
                line: line
            )
        }
        if let failure, !recording {
            XCTFail(failure, file: file, line: line)
        }
    }
}
