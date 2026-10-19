import CoreTestSupport
import Foundation
import HTTPTypes
import OpenAPIRuntime

/// An in-memory `ClientTransport` (iOS TDD §16.1): answers every request with `respond` and
/// records what was sent.
final class InMemoryTransport: ClientTransport, Sendable {
    private let respond: @Sendable (HTTPRequest) async throws -> (HTTPResponse, HTTPBody?)
    private let sent = LockedBox<[HTTPRequest]>([])

    init(respond: @escaping @Sendable (HTTPRequest) async throws -> (HTTPResponse, HTTPBody?)) {
        self.respond = respond
    }

    /// Every request this transport received, in order.
    var requests: [HTTPRequest] {
        sent.value
    }

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        sent.withValue { $0.append(request) }
        return try await respond(request)
    }

    /// The bearer token a request carried, if any.
    static func bearer(of request: HTTPRequest) -> String? {
        guard let value = request.headerFields[.authorization], value.hasPrefix("Bearer ") else {
            return nil
        }
        return String(value.dropFirst("Bearer ".count))
    }

    static func json(_ status: HTTPResponse.Status, _ body: String) -> (HTTPResponse, HTTPBody?) {
        (HTTPResponse(status: status, headerFields: [.contentType: "application/json"]), HTTPBody(body))
    }

    /// A signed-in `GET /api/v1/auth/session` payload, shaped like the server's `SessionDTO`.
    static let sessionJSON = #"""
        {"authenticated": true, "provider": "keycloak",
         "user": {"id": "u-1", "username": "dev", "display_name": "Dev Learner",
                  "email": "dev@example.com", "is_admin": false, "model_allowlist": [],
                  "feature_flags": {"tutor": true, "streaks": false}}}
        """#

    static let unauthorizedJSON = #"""
        {"error": {"code": "unauthenticated", "message": "invalid bearer token", "request_id": "r-1"}}
        """#
}
