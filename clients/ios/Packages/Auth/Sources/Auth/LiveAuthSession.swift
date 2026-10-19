import AppAuth
import Core
import Foundation
import Synchronization
import UIKit
import os

/// The real `AuthSession` (iOS TDD §5.1-§5.3): AppAuth 3.0.0 with PKCE (S256) and a `state`
/// check, an ephemeral `ASWebAuthenticationSession`, and the `aleph://oauth/callback`
/// redirect. The encoded `OIDAuthState` lives in the Keychain (`TokenStore`).
///
/// Main-actor isolated: AppAuth's Objective-C types are not `Sendable`, so they never leave
/// the main actor, and AppAuth calls every callback on the main queue. Only `String` tokens
/// and errors cross into continuations.
@MainActor
public final class LiveAuthSession: AuthSession {
    private let configuration: AuthConfiguration
    private let store: any TokenStore
    private let defaults: UserDefaults
    private let refresher = TokenRefresher()
    /// The latest access token, readable off the main actor by the refresher's check.
    private nonisolated let latestToken = Mutex<String?>(nil)
    private let logger = Logger(subsystem: "com.mattjmcnaughton.aleph", category: "auth")

    private var authState: OIDAuthState?
    private var restored = false
    private var discovered: OIDServiceConfiguration?
    /// The sign-in in progress; AppAuth needs it kept alive until its callback.
    private var currentFlow: (any OIDExternalUserAgentSession)?

    public init(configuration: AuthConfiguration, store: any TokenStore, defaults: UserDefaults = .standard) {
        self.configuration = configuration
        self.store = store
        self.defaults = defaults
    }

    public var state: AuthState {
        restoreIfNeeded()
        return authState?.isAuthorized == true ? .signedIn : .signedOut
    }

    public func signIn() async throws {
        guard let issuer = configuration.issuer, !configuration.clientID.isEmpty else {
            logger.error("Sign-in is not configured for this build")
            throw SignInError.failed
        }
        let serviceConfiguration = try await discover(issuer: issuer)
        guard
            let presenter = Self.presentingViewController(),
            let agent = OIDExternalUserAgentIOS(presenting: presenter, prefersEphemeralSession: true)
        else {
            throw SignInError.failed
        }
        // The convenience initialiser adds a random `state` and PKCE with S256.
        let request = OIDAuthorizationRequest(
            configuration: serviceConfiguration,
            clientId: configuration.clientID,
            scopes: AuthConfiguration.scopes,
            redirectURL: AuthConfiguration.redirectURL,
            responseType: OIDResponseTypeCode,
            additionalParameters: configuration.additionalParameters
        )
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
            currentFlow = OIDAuthState.authState(byPresenting: request, externalUserAgent: agent) { state, error in
                self.currentFlow = nil
                guard let state else {
                    continuation.resume(throwing: Self.signInError(from: error))
                    return
                }
                do {
                    try self.adopt(state)
                    continuation.resume()
                } catch {
                    continuation.resume(throwing: SignInError.failed)
                }
            }
        }
    }

    public func signOut() async {
        restored = true
        authState = nil
        latestToken.withLock { $0 = nil }
        do {
            try store.deleteAll()
        } catch {
            logger.error("Could not clear the Keychain on sign-out")
        }
    }

    public func accessToken() async throws -> String {
        restoreIfNeeded()
        guard let authState else {
            throw AppError.unauthenticated
        }
        return try await freshToken(from: authState, forcingRefresh: false)
    }

    public func refreshAfterUnauthorized(_ rejected: String) async throws -> String {
        try await refresher.refreshedToken(
            replacing: rejected,
            current: { self.latestToken.withLock { $0 } },
            refresh: { try await self.forceRefresh() }
        )
    }

    // MARK: - Tokens

    private func forceRefresh() async throws -> String {
        restoreIfNeeded()
        guard let authState else {
            throw AppError.unauthenticated
        }
        do {
            return try await freshToken(from: authState, forcingRefresh: true)
        } catch AppError.offline {
            throw AppError.offline
        } catch {
            // The refresh token is gone or revoked: the session is over (TDD §5.3).
            await signOut()
            throw AppError.unauthenticated
        }
    }

    /// The current access token, refreshed first when it has expired (or when forced).
    private func freshToken(from authState: OIDAuthState, forcingRefresh: Bool) async throws -> String {
        if forcingRefresh {
            authState.setNeedsTokenRefresh()
        }
        let previous = latestToken.withLock { $0 }
        let token: String = try await withCheckedThrowingContinuation { continuation in
            authState.performAction { accessToken, _, error in
                if let accessToken {
                    continuation.resume(returning: accessToken)
                } else if Self.isNetworkError(error) {
                    continuation.resume(throwing: AppError.offline)
                } else {
                    continuation.resume(throwing: AppError.unauthenticated)
                }
            }
        }
        // A refresh may have rotated the refresh token too: keep the stored copy current.
        if token != previous {
            try? adopt(authState)
        }
        return token
    }

    /// Makes `state` the session and writes it to the Keychain.
    private func adopt(_ state: OIDAuthState) throws {
        authState = state
        restored = true
        let token = state.lastTokenResponse?.accessToken
        latestToken.withLock { $0 = token }
        let data = try NSKeyedArchiver.archivedData(withRootObject: state, requiringSecureCoding: true)
        try store.write(data)
    }

    /// Reads the Keychain once, after the reinstall purge. `App.init` never calls this, so a
    /// prewarmed launch before the first unlock does not touch the Keychain (TDD §1.B1).
    private func restoreIfNeeded() {
        guard !restored else {
            return
        }
        ReinstallPurge.runIfNeeded(defaults: defaults, store: store)
        let data: Data?
        do {
            data = try store.read()
        } catch {
            // Locked before the first unlock: signed out for now, and try again next time.
            logger.error("Could not read the Keychain; treating the session as signed out")
            return
        }
        restored = true
        guard let data else {
            return
        }
        guard let state = try? NSKeyedUnarchiver.unarchivedObject(ofClass: OIDAuthState.self, from: data) else {
            logger.error("Discarding an unreadable stored session")
            try? store.deleteAll()
            return
        }
        authState = state
        let token = state.lastTokenResponse?.accessToken
        latestToken.withLock { $0 = token }
    }

    // MARK: - AppAuth plumbing

    private func discover(issuer: URL) async throws -> OIDServiceConfiguration {
        if let discovered {
            return discovered
        }
        // AppAuth's discovery callback is `Sendable`, so the configuration travels back to the
        // main actor in a box.
        let box: AppAuthBox<OIDServiceConfiguration> = try await withCheckedThrowingContinuation { continuation in
            OIDAuthorizationService.discoverConfiguration(forIssuer: issuer) { configuration, error in
                guard let configuration else {
                    continuation.resume(throwing: Self.signInError(from: error))
                    return
                }
                continuation.resume(returning: AppAuthBox(value: configuration))
            }
        }
        discovered = box.value
        return box.value
    }

    /// The topmost view controller of the foreground scene, to present the sign-in sheet from.
    private static func presentingViewController() -> UIViewController? {
        let scene = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .first { $0.activationState == .foregroundActive }
        var controller = scene?.keyWindow?.rootViewController
        while let presented = controller?.presentedViewController {
            controller = presented
        }
        return controller
    }

    /// Maps an AppAuth error to what the sign-in screen shows.
    nonisolated static func signInError(from error: (any Error)?) -> SignInError {
        guard let error = error.map({ $0 as NSError }) else {
            return .failed
        }
        if error.domain == NSURLErrorDomain {
            return .unavailable
        }
        guard error.domain == OIDGeneralErrorDomain else {
            return .failed
        }
        switch error.code {
        case OIDErrorCode.userCanceledAuthorizationFlow.rawValue,
            OIDErrorCode.programCanceledAuthorizationFlow.rawValue:
            return .cancelled
        case OIDErrorCode.networkError.rawValue:
            return .unavailable
        default:
            return .failed
        }
    }

    nonisolated static func isNetworkError(_ error: (any Error)?) -> Bool {
        signInError(from: error) == .unavailable
    }
}

/// Carries an AppAuth object, which is not `Sendable`, from an AppAuth callback back to the
/// main actor. Unchecked because AppAuth hands the object over and never touches it again
/// (TDD §1.A2 names AppAuth's Objective-C types as the expected exception).
private struct AppAuthBox<Value>: @unchecked Sendable {
    let value: Value
}
