import AppAuth
import Core
import Foundation
import Testing

@testable import Auth

/// The parts of `LiveAuthSession` that need no sign-in sheet: restoring from the Keychain,
/// the purge on a fresh install, sign-out, and how AppAuth errors reach the sign-in screen.
@MainActor
struct LiveAuthSessionTests {
    private let configuration = AuthConfiguration(
        issuer: URL(string: "https://issuer.test"),
        clientID: "aleph-ios",
        audience: nil
    )

    private func session(store: InMemoryTokenStore, defaults: UserDefaults) -> LiveAuthSession {
        LiveAuthSession(configuration: configuration, store: store, defaults: defaults)
    }

    private func freshDefaults() throws -> UserDefaults {
        try #require(UserDefaults(suiteName: "aleph.tests.\(UUID().uuidString)"))
    }

    @Test func aFreshInstallPurgesTheKeychainAndStartsSignedOut() async throws {
        let store = InMemoryTokenStore(Data("left behind by a deleted install".utf8))
        let auth = session(store: store, defaults: try freshDefaults())

        #expect(auth.state == .signedOut)
        #expect(store.data == nil)
    }

    @Test func anUnreadableStoredSessionIsDiscarded() async throws {
        let defaults = try freshDefaults()
        defaults.set(true, forKey: ReinstallPurge.markerKey)
        let store = InMemoryTokenStore(Data("not an archived OIDAuthState".utf8))
        let auth = session(store: store, defaults: defaults)

        #expect(auth.state == .signedOut)
        #expect(store.data == nil)
    }

    @Test func aLockedKeychainReadsAsSignedOutWithoutDiscardingAnything() async throws {
        let defaults = try freshDefaults()
        defaults.set(true, forKey: ReinstallPurge.markerKey)
        let store = InMemoryTokenStore(Data("kept".utf8))
        store.setFailing(true)
        let auth = session(store: store, defaults: defaults)

        #expect(auth.state == .signedOut)
        store.setFailing(false)
        #expect(store.data == Data("kept".utf8))
    }

    @Test func noSessionMeansNoAccessToken() async throws {
        let auth = session(store: InMemoryTokenStore(), defaults: try freshDefaults())

        await #expect(throws: AppError.unauthenticated) {
            try await auth.accessToken()
        }
    }

    @Test func signOutClearsTheKeychain() async throws {
        let defaults = try freshDefaults()
        defaults.set(true, forKey: ReinstallPurge.markerKey)
        let store = InMemoryTokenStore(Data("a session".utf8))
        let auth = session(store: store, defaults: defaults)

        await auth.signOut()

        #expect(store.data == nil)
        #expect(auth.state == .signedOut)
    }

    @Test func signInWithoutAConfiguredIssuerFails() async throws {
        let unconfigured = LiveAuthSession(
            configuration: AuthConfiguration(issuer: nil, clientID: "", audience: nil),
            store: InMemoryTokenStore(),
            defaults: try freshDefaults()
        )

        await #expect(throws: SignInError.failed) {
            try await unconfigured.signIn()
        }
    }

    @Test(arguments: [
        (OIDErrorCode.userCanceledAuthorizationFlow.rawValue, SignInError.cancelled),
        (OIDErrorCode.programCanceledAuthorizationFlow.rawValue, SignInError.cancelled),
        (OIDErrorCode.networkError.rawValue, SignInError.unavailable),
        (OIDErrorCode.serverError.rawValue, SignInError.failed),
    ])
    func appAuthErrorsMapToTheSignInScreensStates(code: Int, expected: SignInError) {
        let error = NSError(domain: OIDGeneralErrorDomain, code: code)
        #expect(LiveAuthSession.signInError(from: error) == expected)
    }

    @Test func aNetworkFailureIsUnavailable() {
        let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)
        #expect(LiveAuthSession.signInError(from: error) == .unavailable)
    }
}
