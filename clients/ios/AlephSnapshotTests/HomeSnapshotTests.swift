import Core
import CoreTestSupport
import SwiftUI
import XCTest

@testable import Features

/// The signed-in shell: an empty Your paths, and the session probe's failure state.
@MainActor
final class HomeSnapshotTests: XCTestCase {
    func testEmptyYourPaths() {
        let view = NavigationStack {
            HomeScreen(account: .learner, onSignOut: {})
        }
        Snapshots.assertStates(of: view, named: "empty", group: "Home")
    }

    func testSessionUnavailable() {
        let view = UnavailableView(error: .offline, retry: {}, signOut: {})
        Snapshots.assertStates(of: view, named: "session-unavailable", group: "Home")
    }
}
