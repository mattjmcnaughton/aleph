import Core
import CoreTestSupport
import Foundation
import Testing

@testable import API

/// The bearer middleware and its single-flight refresh, over an in-memory transport
/// (iOS TDD §5.3, §16.1; PRD §5.1).
struct AuthMiddlewareTests {
    private static let serverURL = URL(string: "https://aleph.test")!

    private func sessionAPI(_ auth: FakeAuthSession, _ transport: InMemoryTransport) -> LiveSessionAPI {
        LiveSessionAPI(client: APIClient.make(serverURL: Self.serverURL, tokens: auth, transport: transport))
    }

    /// 200 for `valid`, 401 for anything else.
    private func server(accepting valid: String) -> InMemoryTransport {
        InMemoryTransport { request in
            InMemoryTransport.bearer(of: request) == valid
                ? InMemoryTransport.json(.ok, InMemoryTransport.sessionJSON)
                : InMemoryTransport.json(.unauthorized, InMemoryTransport.unauthorizedJSON)
        }
    }

    @Test func everyRequestCarriesTheBearerToken() async throws {
        let auth = FakeAuthSession(token: "t1")
        let transport = server(accepting: "t1")

        _ = try await sessionAPI(auth, transport).account()

        #expect(transport.requests.map(InMemoryTransport.bearer(of:)) == ["t1"])
        #expect(await auth.refreshes == 0)
    }

    @Test func a401RefreshesOnceAndRetriesWithTheNewToken() async throws {
        let auth = FakeAuthSession(token: "t1", nextTokens: ["t2"])
        let transport = server(accepting: "t2")

        let account = try await sessionAPI(auth, transport).account()

        #expect(account.displayName == "Dev Learner")
        #expect(transport.requests.map(InMemoryTransport.bearer(of:)) == ["t1", "t2"])
        #expect(await auth.refreshes == 1)
    }

    @Test func concurrent401sShareOneRefresh() async throws {
        let auth = FakeAuthSession(token: "t1", nextTokens: ["t2", "t3"])
        let transport = server(accepting: "t2")
        let api = sessionAPI(auth, transport)

        let names = try await withThrowingTaskGroup(of: String.self) { group in
            for _ in 0..<8 {
                group.addTask { try await api.account().displayName }
            }
            var names: [String] = []
            for try await name in group {
                names.append(name)
            }
            return names
        }

        #expect(names.count == 8)
        #expect(await auth.refreshes == 1)
        // Nothing refreshed twice: no request ever carried the second refresh's token.
        #expect(!transport.requests.map(InMemoryTransport.bearer(of:)).contains("t3"))
    }

    @Test func aSecond401IsNotRetriedAgain() async throws {
        let auth = FakeAuthSession(token: "t1", nextTokens: ["t2", "t3"])
        let transport = server(accepting: "never")

        await #expect(throws: AppError.unauthenticated) {
            try await sessionAPI(auth, transport).account()
        }

        #expect(transport.requests.map(InMemoryTransport.bearer(of:)) == ["t1", "t2"])
        #expect(await auth.refreshes == 1)
    }

    @Test func aFailedRefreshSignsOutAndDoesNotRetry() async throws {
        let auth = FakeAuthSession(token: "t1", nextTokens: [])
        let transport = server(accepting: "t2")

        await #expect(throws: AppError.unauthenticated) {
            try await sessionAPI(auth, transport).account()
        }

        #expect(transport.requests.count == 1)
        #expect(await auth.state == .signedOut)
    }

    @Test func noTokenMeansNoRequest() async throws {
        let auth = FakeAuthSession(token: nil)
        let transport = server(accepting: "t1")

        await #expect(throws: AppError.unauthenticated) {
            try await sessionAPI(auth, transport).account()
        }

        #expect(transport.requests.isEmpty)
    }
}
