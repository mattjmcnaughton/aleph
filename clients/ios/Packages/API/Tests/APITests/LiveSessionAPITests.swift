import Core
import CoreTestSupport
import Foundation
import HTTPTypes
import Testing

@testable import API

/// The session probe's adapter and error mapping (iOS TDD §4.4, §5.4).
struct LiveSessionAPITests {
    private static let serverURL = URL(string: "https://aleph.test")!

    /// The probe against a server that always answers `status` with `body`.
    private func api(status: Int, body: String) -> LiveSessionAPI {
        let transport = InMemoryTransport { _ in
            InMemoryTransport.json(HTTPResponse.Status(code: status), body)
        }
        let tokens = FakeAuthSession(token: "t1")
        return LiveSessionAPI(client: APIClient.make(serverURL: Self.serverURL, tokens: tokens, transport: transport))
    }

    @Test func mapsTheSessionUserToAnAccount() async throws {
        let account = try await api(status: 200, body: InMemoryTransport.sessionJSON).account()

        #expect(account.id == "u-1")
        #expect(account.username == "dev")
        #expect(account.displayName == "Dev Learner")
        #expect(account.email == "dev@example.com")
        #expect(!account.isAdmin)
        #expect(account.featureFlags.isOn(.tutor))
        #expect(!account.featureFlags.isOn(.streaks))
        #expect(!account.featureFlags.isOn(.flow))
    }

    @Test func aSignedOutSessionIsUnauthenticated() async throws {
        let body = #"{"authenticated": false, "provider": "keycloak", "user": null}"#

        await #expect(throws: AppError.unauthenticated) {
            try await api(status: 200, body: body).account()
        }
    }

    @Test(arguments: [
        (404, AppError.notFound),
        (409, AppError.conflict(reason: "busy")),
        (422, AppError.validation(message: "Bad input")),
        (429, AppError.rateLimited),
        (503, AppError.serviceUnavailable),
        (500, AppError.server(status: 500)),
    ])
    func mapsTheErrorEnvelope(status: Int, expected: AppError) async throws {
        let body = #"""
            {"error": {"code": "x", "message": "Bad input", "request_id": "r-1", "details": {"reason": "busy"}}}
            """#

        await #expect(throws: expected) {
            try await api(status: status, body: body).account()
        }
    }

    @Test func mapsAnOfflineTransportToOffline() throws {
        #expect(try ErrorMapping.appError(from: URLError(.notConnectedToInternet)) == .offline)
        #expect(try ErrorMapping.appError(from: URLError(.timedOut)) == .timedOut)
    }

    @Test func cancellationIsRethrownNotMapped() {
        #expect(throws: CancellationError.self) {
            try ErrorMapping.appError(from: URLError(.cancelled))
        }
    }
}
