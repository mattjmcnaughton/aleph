#if ALEPH_UI_TESTING
    import Core
    import Foundation

    /// The UI-test build's `AuthSession` (iOS TDD §5.5, D22). Compiled only under the
    /// `ALEPH_UI_TESTING` condition, which only the `UITest` build configuration sets, so the
    /// Release binary carries neither this type nor its launch argument (ios-gate checks).
    ///
    /// Tokens come from the launch argument `-AlephTestTokens <json array>`, pre-signed by the
    /// e2e fake issuer (`clients/ios/AlephUITests/Fixtures/tokens.json`). `accessToken()`
    /// returns the first; a `401` advances to the next, so a test can drive the real
    /// middleware's refresh path. Interactive sign-in does not exist here.
    actor UITestAuthSession: AuthSession {
        static let launchArgument = "-AlephTestTokens"

        private var tokens: [String]

        init(tokens: [String]) {
            self.tokens = tokens
        }

        /// The tokens after `-AlephTestTokens`, or `nil` when the argument is absent.
        static func tokens(fromLaunchArguments arguments: [String]) -> [String]? {
            guard let index = arguments.firstIndex(of: launchArgument), index + 1 < arguments.count else {
                return nil
            }
            return try? JSONDecoder().decode([String].self, from: Data(arguments[index + 1].utf8))
        }

        var state: AuthState {
            tokens.isEmpty ? .signedOut : .signedIn
        }

        func signIn() async throws {
            throw SignInError.failed
        }

        func signOut() async {
            tokens.removeAll()
        }

        func accessToken() async throws -> String {
            guard let token = tokens.first else {
                throw AppError.unauthenticated
            }
            return token
        }

        func refreshAfterUnauthorized(_ rejected: String) async throws -> String {
            if tokens.first == rejected {
                tokens.removeFirst()
            }
            guard let next = tokens.first else {
                throw AppError.unauthenticated
            }
            return next
        }
    }
#endif
