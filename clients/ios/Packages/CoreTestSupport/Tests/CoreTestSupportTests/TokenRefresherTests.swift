import Core
import CoreTestSupport
import Testing

/// Single-flight refresh (iOS TDD §5.3): concurrent 401s share one refresh.
struct TokenRefresherTests {
    @Test func concurrentRejectionsShareOneRefresh() async throws {
        let refresher = TokenRefresher()
        let stored = LockedBox<String?>("old")
        let refreshes = LockedBox(0)

        let tokens = try await withThrowingTaskGroup(of: String.self) { group in
            for _ in 0..<8 {
                group.addTask {
                    try await refresher.refreshedToken(
                        replacing: "old",
                        current: { stored.value },
                        refresh: {
                            refreshes.withValue { $0 += 1 }
                            await Task.yield()
                            stored.value = "new"
                            return "new"
                        }
                    )
                }
            }
            var tokens: [String] = []
            for try await token in group {
                tokens.append(token)
            }
            return tokens
        }

        #expect(refreshes.value == 1)
        #expect(tokens == Array(repeating: "new", count: 8))
    }

    @Test func aRejectionOfAnAlreadyReplacedTokenDoesNotRefreshAgain() async throws {
        let refresher = TokenRefresher()
        let refreshes = LockedBox(0)

        let token = try await refresher.refreshedToken(
            replacing: "old",
            current: { "new" },
            refresh: {
                refreshes.withValue { $0 += 1 }
                return "newer"
            }
        )

        #expect(token == "new")
        #expect(refreshes.value == 0)
    }

    @Test func aFailedRefreshFailsEveryWaiterAndTheNextRejectionTriesAgain() async throws {
        let refresher = TokenRefresher()
        let refreshes = LockedBox(0)

        await #expect(throws: AppError.unauthenticated) {
            try await refresher.refreshedToken(
                replacing: "old",
                current: { "old" },
                refresh: {
                    refreshes.withValue { $0 += 1 }
                    throw AppError.unauthenticated
                }
            )
        }
        let token = try await refresher.refreshedToken(
            replacing: "old",
            current: { "old" },
            refresh: {
                refreshes.withValue { $0 += 1 }
                return "new"
            }
        )

        #expect(token == "new")
        #expect(refreshes.value == 2)
    }
}
