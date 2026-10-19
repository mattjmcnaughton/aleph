/// Whether the app holds tokens (iOS TDD §5.1). The Account itself comes from the session
/// probe (`SessionAPI`), never from the token.
public enum AuthState: Equatable, Sendable {
    case signedOut
    case signedIn
}

/// How an interactive sign-in can end without tokens.
public enum SignInError: Error, Equatable, Sendable {
    /// The learner closed the sheet: back to the launch screen with no message (PRD §5.1).
    case cancelled
    /// The identity provider could not be reached.
    case unavailable
    /// Anything else: a provider error, a bad configuration, a failed code exchange.
    case failed
}

/// Supplies the bearer token every API request carries (iOS TDD §5.3).
public protocol TokenProvider: Sendable {
    /// A valid access token, refreshed first if it has expired. Throws when there is none.
    func accessToken() async throws -> String

    /// The server answered `401` to `rejected`. Joins the refresh in flight or starts one,
    /// and returns the new token. When the refresh fails, the session signs out and this
    /// throws.
    func refreshAfterUnauthorized(_ rejected: String) async throws -> String
}

/// The app's OIDC session (iOS TDD §5.1). `LiveAuthSession` wraps AppAuth; tests and the
/// UI-test build use other implementations. Screens depend on this protocol only.
public protocol AuthSession: TokenProvider {
    var state: AuthState { get async }

    /// Presents the identity provider's sign-in sheet. Throws `SignInError`.
    func signIn() async throws

    /// Forgets every token, in memory and in the Keychain.
    func signOut() async
}

/// `GET /api/v1/auth/session` (PRD §5.1, §5.9).
public protocol SessionAPI: Sendable {
    /// The signed-in Account. Throws `AppError`: `.unauthenticated` once a refresh has failed.
    func account() async throws -> Account
}
