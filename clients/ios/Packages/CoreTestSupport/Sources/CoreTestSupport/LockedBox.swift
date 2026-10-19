import Synchronization

/// A value shared between test tasks, guarded by a `Mutex`.
public final class LockedBox<Value: Sendable>: Sendable {
    private let mutex: Mutex<Value>

    public init(_ value: Value) {
        mutex = Mutex(value)
    }

    public var value: Value {
        get { mutex.withLock { $0 } }
        set { mutex.withLock { $0 = newValue } }
    }

    /// Reads and changes the value in one critical section.
    public func withValue<Result: Sendable>(_ body: (inout Value) -> Result) -> Result {
        mutex.withLock { body(&$0) }
    }
}
