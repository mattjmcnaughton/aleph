import Core
import CoreTestSupport
import Testing

@testable import Features

/// The sign-in screen's states (iOS TDD §6.3, PRD §5.1). Web: `routes/login.tsx`, whose
/// failure copy ("Sign-in didn't complete. Try again.") this mirrors.
struct SignInModelTests {
    @Test func aSuccessfulSignInRunsTheSessionProbe() async {
        let auth = FakeAuthSession(token: nil, signIn: .succeed(token: "t1"))
        let probed = LockedBox(false)
        let model = SignInModel(auth: auth) { probed.value = true }

        await model.signIn()

        #expect(probed.value)
        #expect(model.state == .idle)
        #expect(await auth.state == .signedIn)
    }

    @Test func cancellingTheSheetReturnsToIdleWithNoMessage() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .fail(.cancelled))) {}

        await model.signIn()

        #expect(model.state == .idle)
    }

    @Test func aFailureShowsAShortMessageAndTheButtonAgain() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .fail(.failed))) {}

        await model.signIn()

        #expect(model.state == .failed(message: "Sign-in didn't complete. Try again."))
    }

    @Test func anUnreachableProviderSaysSo() async {
        let model = SignInModel(auth: FakeAuthSession(signIn: .fail(.unavailable))) {}

        await model.signIn()

        #expect(model.state == .failed(message: "Can't reach sign-in right now. Try again."))
    }

    @Test func aSecondTapWhileSigningInStartsNothing() async {
        let auth = FakeAuthSession(signIn: .hang)
        let model = SignInModel(auth: auth) {}
        let first = Task { await model.signIn() }
        while model.state != .signingIn {
            await Task.yield()
        }

        await model.signIn()

        #expect(await auth.signInCalls == 1)
        first.cancel()
        await first.value
        #expect(model.state == .idle)
    }
}
