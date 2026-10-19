import Core
import Observation

/// The launch screen's one action (iOS TDD §6.3, PRD §5.1). Cancelling the sheet returns to
/// `idle` with no message; a failure shows a short message and the button again. Web:
/// `routes/login.tsx`.
@Observable
final class SignInModel {
    nonisolated enum State: Equatable, Sendable {
        case idle
        case signingIn
        case failed(message: String)
    }

    private(set) var state: State = .idle

    private let auth: any AuthSession
    private let onSignedIn: @MainActor () async -> Void

    /// - Parameter onSignedIn: runs after the sheet succeeds; the session probe.
    init(auth: any AuthSession, onSignedIn: @escaping @MainActor () async -> Void) {
        self.auth = auth
        self.onSignedIn = onSignedIn
    }

    func signIn() async {
        guard state != .signingIn else {
            return
        }
        state = .signingIn
        do {
            try await auth.signIn()
            await onSignedIn()
            state = .idle
        } catch SignInError.cancelled {
            state = .idle
        } catch is CancellationError {
            state = .idle
        } catch SignInError.unavailable {
            state = .failed(message: String(localized: "Can't reach sign-in right now. Try again.", bundle: .module))
        } catch {
            state = .failed(message: String(localized: "Sign-in didn't complete. Try again.", bundle: .module))
        }
    }
}
