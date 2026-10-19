import Core
import Observation

/// Who is signed in, and their feature flags (iOS TDD §5.4, PRD §5.1, §5.9).
///
/// The Account comes from the session probe, `GET /api/v1/auth/session`, read at launch when
/// tokens exist, after a sign-in, and every time the scene becomes `.active`, so a flag change
/// or a kill switch reaches the app without a reinstall. Web: the root route's `beforeLoad`
/// (`authRedirect`, `clients/web/src/lib/auth.ts`) and `useFeatureFlag`.
@Observable
public final class SessionStore {
    nonisolated public enum State: Equatable, Sendable {
        /// Deciding: reading the Keychain, then the probe.
        case launching
        case signedOut
        case signedIn(Account)
        /// Tokens exist but the probe failed (offline, a server error): Retry.
        case unavailable(AppError)
    }

    public private(set) var state: State = .launching

    private let auth: any AuthSession
    private let session: any SessionAPI

    public init(auth: any AuthSession, session: any SessionAPI) {
        self.auth = auth
        self.session = session
    }

    /// The signed-in Account's flags. Every flag is off until the probe has answered, so a
    /// gated surface never flashes open (web: `useFeatureFlag`).
    public var flags: FeatureFlags {
        if case .signedIn(let account) = state {
            account.featureFlags
        } else {
            .allOff
        }
    }

    /// At launch: signed out without tokens, otherwise probe.
    public func start() async {
        guard await auth.state == .signedIn else {
            state = .signedOut
            return
        }
        await probe()
    }

    /// After an interactive sign-in succeeded.
    public func didSignIn() async {
        await probe()
    }

    /// The scene became `.active` (TDD §1.B1): re-read the session and its flags.
    public func sceneBecameActive() async {
        switch state {
        case .signedIn, .unavailable:
            await probe()
        case .launching, .signedOut:
            return
        }
    }

    /// Retry after `.unavailable`.
    public func retry() async {
        await probe()
    }

    public func signOut() async {
        await auth.signOut()
        state = .signedOut
    }

    private func probe() async {
        do {
            let account = try await session.account()
            state = .signedIn(account)
        } catch AppError.unauthenticated {
            // The refresh already failed in the middleware: the session is over.
            await auth.signOut()
            state = .signedOut
        } catch is CancellationError {
            return
        } catch {
            // A transient failure keeps the last good session and its flags.
            if case .signedIn = state {
                return
            }
            state = .unavailable(error as? AppError ?? .server(status: 0))
        }
    }
}
