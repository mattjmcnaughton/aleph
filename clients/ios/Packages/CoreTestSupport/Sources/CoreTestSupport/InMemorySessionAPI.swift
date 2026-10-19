import Core

/// An in-memory `SessionAPI`: answers each probe with the next queued response, repeating
/// the last one, and counts the reads.
public actor InMemorySessionAPI: SessionAPI {
    private var responses: [Result<Account, AppError>]
    public private(set) var reads = 0

    /// - Parameter responses: one per probe, in order; the last repeats. Must not be empty.
    public init(_ responses: [Result<Account, AppError>] = [.success(.learner)]) {
        precondition(!responses.isEmpty, "InMemorySessionAPI needs at least one response")
        self.responses = responses
    }

    public func account() async throws -> Account {
        reads += 1
        let response = responses.count > 1 ? responses.removeFirst() : responses[0]
        return try response.get()
    }

    /// Replaces the queue, e.g. to change the flags before the next probe.
    public func respond(with responses: [Result<Account, AppError>]) {
        precondition(!responses.isEmpty, "InMemorySessionAPI needs at least one response")
        self.responses = responses
    }
}
