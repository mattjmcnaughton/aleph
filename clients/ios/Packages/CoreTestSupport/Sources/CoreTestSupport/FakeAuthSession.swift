import Core

/// An in-memory `AuthSession` (iOS TDD §16.1). It holds one access token, refreshes through
/// the real `TokenRefresher` from a queue of next tokens, and counts what was asked of it.
public actor FakeAuthSession: AuthSession {
    /// What `signIn()` does.
    public enum SignInBehavior: Sendable {
        /// Succeeds and stores this access token.
        case succeed(token: String)
        /// Throws this error.
        case fail(SignInError)
        /// Never returns: a sign-in sheet left open.
        case hang
    }

    public private(set) var signInCalls = 0
    public private(set) var signOutCalls = 0
    /// Refreshes actually performed (joined and skipped ones do not count).
    public private(set) var refreshes = 0

    private let token: LockedBox<String?>
    private var nextTokens: [String]
    private let signInBehavior: SignInBehavior
    private let refresher = TokenRefresher()

    /// - Parameters:
    ///   - token: the stored access token; `nil` is signed out.
    ///   - nextTokens: what each refresh returns, in order. A refresh with none left fails
    ///     and signs out.
    ///   - signIn: what `signIn()` does.
    public init(
        token: String? = nil,
        nextTokens: [String] = [],
        signIn: SignInBehavior = .succeed(token: "signed-in-token")
    ) {
        self.token = LockedBox(token)
        self.nextTokens = nextTokens
        self.signInBehavior = signIn
    }

    public var state: AuthState {
        token.value == nil ? .signedOut : .signedIn
    }

    public func signIn() async throws {
        signInCalls += 1
        switch signInBehavior {
        case .succeed(let newToken):
            token.value = newToken
        case .fail(let error):
            throw error
        case .hang:
            try await Task.sleep(for: .seconds(86_400))
        }
    }

    public func signOut() async {
        signOutCalls += 1
        token.value = nil
    }

    public func accessToken() async throws -> String {
        guard let value = token.value else {
            throw AppError.unauthenticated
        }
        return value
    }

    public func refreshAfterUnauthorized(_ rejected: String) async throws -> String {
        let token = self.token
        return try await refresher.refreshedToken(
            replacing: rejected,
            current: { token.value },
            refresh: { try await self.performRefresh() }
        )
    }

    private func performRefresh() async throws -> String {
        refreshes += 1
        // Let concurrent rejections arrive while this refresh is in flight.
        await Task.yield()
        guard !nextTokens.isEmpty else {
            token.value = nil
            throw AppError.unauthenticated
        }
        let next = nextTokens.removeFirst()
        token.value = next
        return next
    }
}
