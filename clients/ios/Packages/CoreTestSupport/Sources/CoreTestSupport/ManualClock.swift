import Synchronization

/// A `Clock` that moves only when a test advances it (iOS TDD §6.2, §16.1).
///
/// `sleep(until:)` suspends until `advance(by:)` moves `now` past the deadline; a cancelled
/// sleeper throws `CancellationError` at once. Screen models take `any Clock<Duration>`, so
/// a poll's backoff and stall times run in no real time under test.
public final class ManualClock: Clock, Sendable {
    public struct Instant: InstantProtocol {
        public var offset: Swift.Duration

        public init(offset: Swift.Duration = .zero) {
            self.offset = offset
        }

        public func advanced(by duration: Swift.Duration) -> Instant {
            Instant(offset: offset + duration)
        }

        public func duration(to other: Instant) -> Swift.Duration {
            other.offset - offset
        }

        public static func < (lhs: Instant, rhs: Instant) -> Bool {
            lhs.offset < rhs.offset
        }
    }

    private struct Sleeper {
        let id: Int
        let deadline: Instant
        let continuation: CheckedContinuation<Void, any Error>
    }

    private struct State {
        var now = Instant()
        var nextID = 0
        var sleepers: [Sleeper] = []
        /// Sleepers cancelled before they registered.
        var cancelled: Set<Int> = []
    }

    private let state = Mutex(State())

    public init() {}

    public var now: Instant {
        state.withLock { $0.now }
    }

    public var minimumResolution: Swift.Duration {
        .zero
    }

    /// How many tasks are asleep on this clock.
    public var sleeperCount: Int {
        state.withLock { $0.sleepers.count }
    }

    public func sleep(until deadline: Instant, tolerance: Swift.Duration? = nil) async throws {
        let id = state.withLock { state in
            state.nextID += 1
            return state.nextID
        }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                let outcome: Result<Void, any Error>? = state.withLock { state in
                    if state.cancelled.remove(id) != nil {
                        return .failure(CancellationError())
                    }
                    if deadline <= state.now {
                        return .success(())
                    }
                    state.sleepers.append(Sleeper(id: id, deadline: deadline, continuation: continuation))
                    return nil
                }
                if let outcome {
                    continuation.resume(with: outcome)
                }
            }
        } onCancel: {
            let sleeper: Sleeper? = state.withLock { state in
                if let index = state.sleepers.firstIndex(where: { $0.id == id }) {
                    return state.sleepers.remove(at: index)
                }
                state.cancelled.insert(id)
                return nil
            }
            sleeper?.continuation.resume(throwing: CancellationError())
        }
    }

    /// Moves time forward and wakes every sleeper whose deadline has passed, earliest first,
    /// then yields so the woken tasks run before the caller continues.
    public func advance(by duration: Swift.Duration) async {
        let due: [Sleeper] = state.withLock { state in
            state.now = state.now.advanced(by: duration)
            let now = state.now
            let due = state.sleepers.filter { $0.deadline <= now }.sorted { $0.deadline < $1.deadline }
            state.sleepers.removeAll { $0.deadline <= now }
            return due
        }
        for sleeper in due {
            sleeper.continuation.resume()
        }
        for _ in 0..<10 {
            await Task.yield()
        }
    }
}
