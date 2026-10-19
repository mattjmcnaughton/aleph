import CoreTestSupport
import SwiftUI
import XCTest

@testable import Features

/// The launch and sign-in states (PRD §5.1, iOS TDD §6.3), each from a fake that puts the
/// model in that state.
@MainActor
final class SignInSnapshotTests: XCTestCase {
    func testLaunch() {
        Snapshots.assertStates(of: LaunchView(), named: "launch", group: "SignIn")
    }

    func testIdle() {
        let model = SignInModel(auth: FakeAuthSession()) {}
        Snapshots.assertStates(of: SignInScreen(model: model), named: "idle", group: "SignIn")
    }

    func testSigningIn() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .hang)) {}
        let signingIn = Task { await model.signIn() }
        while model.state != .signingIn {
            await Task.yield()
        }

        Snapshots.assertStates(of: SignInScreen(model: model), named: "signing-in", group: "SignIn")
        signingIn.cancel()
    }

    func testFailed() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .fail(.failed))) {}
        await model.signIn()

        Snapshots.assertStates(of: SignInScreen(model: model), named: "failed", group: "SignIn")
    }

    func testProviderUnavailable() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .fail(.unavailable))) {}
        await model.signIn()

        Snapshots.assertStates(of: SignInScreen(model: model), named: "unavailable", group: "SignIn")
    }
}
