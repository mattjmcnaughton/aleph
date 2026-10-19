internal import Core
internal import Foundation
import HTTPTypes
internal import OpenAPIRuntime

/// Bearer auth for every request (iOS TDD §5.3, PRD §5.1).
///
/// Attaches `Authorization: Bearer <access token>`. On a `401` it asks the `TokenProvider`
/// for a refreshed token (concurrent `401`s share one refresh) and retries **once**; the
/// retry's response is returned whatever it is. Never a loop, and never a request without
/// the header: no token, or a failed refresh, throws instead (`AppError.unauthenticated`, or
/// `.offline` when the refresh could not reach the provider).
struct AuthMiddleware: ClientMiddleware {
    /// Request bodies are small JSON; one is buffered so the retry can resend it.
    static let maxBufferedBodyBytes = 1_048_576

    let tokens: any TokenProvider

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var bufferedBody: Data?
        if let body {
            bufferedBody = try await Data(collecting: body, upTo: Self.maxBufferedBodyBytes)
        }

        let token: String
        do {
            token = try await tokens.accessToken()
        } catch {
            throw Self.authFailure(error)
        }
        let (response, responseBody) = try await next(
            Self.authorized(request, with: token),
            bufferedBody.map { HTTPBody($0) },
            baseURL
        )
        guard response.status == .unauthorized else {
            return (response, responseBody)
        }

        let refreshed: String
        do {
            refreshed = try await tokens.refreshAfterUnauthorized(token)
        } catch {
            throw Self.authFailure(error)
        }
        return try await next(Self.authorized(request, with: refreshed), bufferedBody.map { HTTPBody($0) }, baseURL)
    }

    /// A token failure keeps its `AppError` (`.offline` must not sign anyone out); anything
    /// else ends the session.
    private static func authFailure(_ error: any Error) -> any Error {
        if error is CancellationError || error is AppError {
            return error
        }
        return AppError.unauthenticated
    }

    private static func authorized(_ request: HTTPRequest, with token: String) -> HTTPRequest {
        var request = request
        request.headerFields[.authorization] = "Bearer \(token)"
        return request
    }
}
