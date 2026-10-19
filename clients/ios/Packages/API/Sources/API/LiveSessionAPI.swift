public import Core
public import Foundation
internal import OpenAPIRuntime
internal import OpenAPIURLSession

/// `GET /api/v1/auth/session` over the generated client (PRD §5.1, §5.9).
public struct LiveSessionAPI: SessionAPI {
    private let client: Client

    /// - Parameters:
    ///   - serverURL: the API's origin, e.g. `https://aleph.example`.
    ///   - tokens: supplies the bearer token and its refresh.
    public init(serverURL: URL, tokens: any TokenProvider) {
        self.init(client: APIClient.make(serverURL: serverURL, tokens: tokens, transport: URLSessionTransport()))
    }

    init(client: Client) {
        self.client = client
    }

    public func account() async throws -> Account {
        let output: Operations.GetAuthSession.Output
        do {
            output = try await client.getAuthSession()
        } catch {
            throw try ErrorMapping.appError(from: error)
        }
        switch output {
        case .ok(let ok):
            let session = try ok.body.json
            // A bearer request never reads as signed out (api.md); treat one as a 401.
            guard session.authenticated, let user = session.user else {
                throw AppError.unauthenticated
            }
            return Account(user)
        case .default(let statusCode, let response):
            throw ErrorMapping.appError(status: statusCode, envelope: try? response.body.json)
        }
    }
}

/// Builds the generated client with the app's middleware.
enum APIClient {
    static func make(serverURL: URL, tokens: any TokenProvider, transport: any ClientTransport) -> Client {
        Client(serverURL: serverURL, transport: transport, middlewares: [AuthMiddleware(tokens: tokens)])
    }
}

extension Account {
    /// The adapter from the generated `UserDTO` (iOS TDD §4.4).
    init(_ user: Components.Schemas.UserDTO) {
        self.init(
            id: user.id,
            username: user.username,
            displayName: user.displayName,
            email: user.email,
            isAdmin: user.isAdmin ?? false,
            featureFlags: FeatureFlags(values: user.featureFlags?.additionalProperties ?? [:])
        )
    }
}
