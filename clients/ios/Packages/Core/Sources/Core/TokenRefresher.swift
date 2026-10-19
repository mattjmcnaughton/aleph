/// Single-flight token refresh (iOS TDD §5.3).
///
/// Concurrent requests that were all rejected with the same token share one refresh. A
/// request whose rejected token has already been replaced (another request refreshed first)
/// gets the current token without a second refresh. A failed refresh fails every waiter and
/// leaves nothing in flight, so the next rejection can try again.
public actor TokenRefresher {
    private var inFlight: Task<String, any Error>?

    public init() {}

    /// - Parameters:
    ///   - rejected: the access token the server answered `401` to.
    ///   - current: reads the token stored now. Synchronous, so no other caller can slip in
    ///     between this check and starting a refresh.
    ///   - refresh: performs the refresh, **stores** the new token (so `current` returns it)
    ///     and returns it.
    public func refreshedToken(
        replacing rejected: String,
        current: @Sendable () -> String?,
        refresh: @escaping @Sendable () async throws -> String
    ) async throws -> String {
        if let inFlight {
            return try await inFlight.value
        }
        if let stored = current(), stored != rejected {
            return stored
        }
        let task = Task { try await refresh() }
        inFlight = task
        defer { inFlight = nil }
        return try await task.value
    }
}
