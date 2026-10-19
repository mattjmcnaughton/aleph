import Core
import CoreTestSupport
import Testing

@testable import Features

/// The session probe and flag gating (iOS TDD §5.4, §1.B11; PRD §5.1, §5.9). Mirrors the
/// web's `clients/web/src/lib/auth.test.ts` (signed out -> sign-in, signed in -> the app) and
/// `clients/web/src/lib/feature-flags.test.tsx` (unknown and unsettled flags are off).
struct SessionStoreTests {
    @Test func withoutTokensTheAppIsSignedOutAndNeverProbes() async {
        let session = InMemorySessionAPI()
        let store = SessionStore(auth: FakeAuthSession(token: nil), session: session)

        await store.start()

        #expect(store.state == .signedOut)
        #expect(await session.reads == 0)
    }

    @Test func withTokensTheProbeSignsTheLearnerIn() async {
        let store = SessionStore(auth: FakeAuthSession(token: "t1"), session: InMemorySessionAPI())

        await store.start()

        #expect(store.state == .signedIn(.learner))
    }

    @Test func everyFlagIsOffBeforeTheProbeAnswers() {
        let store = SessionStore(
            auth: FakeAuthSession(token: "t1"),
            session: InMemorySessionAPI([.success(.learner(flags: ["tutor": true]))])
        )

        #expect(store.state == .launching)
        #expect(!store.flags.isOn(.tutor))
    }

    @Test func theFlagsAreTheProbes() async {
        let store = SessionStore(
            auth: FakeAuthSession(token: "t1"),
            session: InMemorySessionAPI([.success(.learner(flags: ["tutor": true, "flow": false]))])
        )

        await store.start()

        #expect(store.flags.isOn(.tutor))
        #expect(!store.flags.isOn(.flow))
        #expect(!store.flags.isOn(.analyst))
    }

    @Test func theFlagsAreReReadWhenTheSceneBecomesActive() async {
        let session = InMemorySessionAPI([.success(.learner(flags: ["tutor": true]))])
        let store = SessionStore(auth: FakeAuthSession(token: "t1"), session: session)
        await store.start()
        #expect(store.flags.isOn(.tutor))

        // A kill switch flipped while the app was in the background.
        await session.respond(with: [.success(.learner(flags: ["tutor": false]))])
        await store.sceneBecameActive()

        #expect(!store.flags.isOn(.tutor))
        #expect(await session.reads == 2)
    }

    @Test func aSignedOutAppDoesNotProbeOnActive() async {
        let session = InMemorySessionAPI()
        let store = SessionStore(auth: FakeAuthSession(token: nil), session: session)
        await store.start()

        await store.sceneBecameActive()

        #expect(await session.reads == 0)
    }

    @Test func aFailedRefreshSignsOut() async {
        let auth = FakeAuthSession(token: "t1")
        let store = SessionStore(auth: auth, session: InMemorySessionAPI([.failure(.unauthenticated)]))

        await store.start()

        #expect(store.state == .signedOut)
        #expect(await auth.signOutCalls == 1)
    }

    @Test func aTransientFailureOnActiveKeepsTheSessionAndItsFlags() async {
        let session = InMemorySessionAPI([.success(.learner(flags: ["tutor": true])), .failure(.offline)])
        let store = SessionStore(auth: FakeAuthSession(token: "t1"), session: session)
        await store.start()

        await store.sceneBecameActive()

        #expect(store.state == .signedIn(.learner(flags: ["tutor": true])))
        #expect(store.flags.isOn(.tutor))
    }

    @Test func aFailedLaunchProbeIsUnavailableUntilRetried() async {
        let session = InMemorySessionAPI([.failure(.offline), .success(.learner)])
        let store = SessionStore(auth: FakeAuthSession(token: "t1"), session: session)

        await store.start()
        #expect(store.state == .unavailable(.offline))

        await store.retry()
        #expect(store.state == .signedIn(.learner))
    }

    @Test func signingOutForgetsTheSession() async {
        let auth = FakeAuthSession(token: "t1")
        let store = SessionStore(auth: auth, session: InMemorySessionAPI())
        await store.start()

        await store.signOut()

        #expect(store.state == .signedOut)
        #expect(!store.flags.isOn(.tutor))
        #expect(await auth.state == .signedOut)
    }
}
