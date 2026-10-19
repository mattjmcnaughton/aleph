import SnapshotTesting
import SwiftUI
import XCTest

/// Snapshot helpers (iOS TDD §16.2), adapted from the PoC.
///
/// Every screen state is recorded twice: dark at the default text size on an iPhone 17
/// canvas, and dark at `.accessibility3` (`.accessibilityExtraLarge`) on a double-height
/// canvas so the whole screen is visible. Determinism: the pinned simulator (iPhone 17,
/// iOS 26.5), `en_US`, UTC, animations off, and the PoC's tolerance.
///
/// References live in `__Snapshots__/<group>/` beside this file and are committed.
/// - Default (compare): a mismatch or a missing reference fails the test; the failing image
///   goes to `SNAPSHOT_ARTIFACTS`.
/// - `SNAPSHOT_RECORD=1` (ios-dev branches): every reference is re-recorded; recording never
///   fails.
@MainActor
enum Snapshots {
    private static var environment: [String: String] { ProcessInfo.processInfo.environment }
    private static var recording: Bool { environment["SNAPSHOT_RECORD"] == "1" }

    /// iPhone 17: 402 x 874 points.
    private static let size = CGSize(width: 402, height: 874)
    private static let safeArea = UIEdgeInsets(top: 62, left: 0, bottom: 34, right: 0)

    static func assertStates(
        of view: some View,
        named name: String,
        group: String,
        file: StaticString = #filePath,
        line: UInt = #line
    ) {
        UIView.setAnimationsEnabled(false)
        for (suffix, category, height) in [
            ("dark", UIContentSizeCategory.large, size.height),
            ("dark-ax3", UIContentSizeCategory.accessibilityExtraLarge, size.height * 2),
        ] {
            verify(
                view,
                name: "\(name)-\(suffix)",
                group: group,
                category: category,
                canvas: CGSize(width: size.width, height: height),
                file: file,
                line: line
            )
        }
    }

    private static func verify(
        _ view: some View,
        name: String,
        group: String,
        category: UIContentSizeCategory,
        canvas: CGSize,
        file: StaticString,
        line: UInt
    ) {
        let traits = UITraitCollection { traits in
            traits.userInterfaceStyle = .dark
            traits.preferredContentSizeCategory = category
        }
        let content =
            view
            .environment(\.locale, Locale(identifier: "en_US"))
            .environment(\.timeZone, TimeZone(identifier: "UTC") ?? .current)
            .dynamicTypeSize(DynamicTypeSize(category) ?? .large)
            .preferredColorScheme(.dark)
        let directory = URL(fileURLWithPath: "\(file)")
            .deletingLastPathComponent()
            .appendingPathComponent("__Snapshots__")
            .appendingPathComponent(group)
            .path

        let failure = withSnapshotTesting(record: recording ? .all : .never) {
            verifySnapshot(
                of: content,
                // Tolerate sub-pixel anti-aliasing noise; real changes still fail.
                as: .image(
                    precision: 0.995,
                    perceptualPrecision: 0.98,
                    layout: .device(config: ViewImageConfig(safeArea: safeArea, size: canvas, traits: traits)),
                    traits: traits
                ),
                named: name,
                snapshotDirectory: directory,
                file: file,
                testName: group,
                line: line
            )
        }
        if let failure, !recording {
            XCTFail(failure, file: file, line: line)
        }
    }
}
