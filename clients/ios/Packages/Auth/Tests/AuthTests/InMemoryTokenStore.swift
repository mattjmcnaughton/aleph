import Auth
import CoreTestSupport
import Foundation
import Security

/// An in-memory `TokenStore` that can be told to fail, as a locked Keychain does.
final class InMemoryTokenStore: TokenStore {
    private let stored: LockedBox<Data?>
    private let failing = LockedBox(false)
    private let deletes = LockedBox(0)

    init(_ data: Data? = nil) {
        stored = LockedBox(data)
    }

    var data: Data? {
        stored.value
    }

    var deleteCount: Int {
        deletes.value
    }

    /// While set, every call throws, like a Keychain read before the first unlock.
    func setFailing(_ value: Bool) {
        failing.value = value
    }

    func read() throws -> Data? {
        try checkAvailable()
        return stored.value
    }

    func write(_ data: Data) throws {
        try checkAvailable()
        stored.value = data
    }

    func deleteAll() throws {
        try checkAvailable()
        deletes.withValue { $0 += 1 }
        stored.value = nil
    }

    private func checkAvailable() throws {
        if failing.value {
            throw KeychainError(status: errSecInteractionNotAllowed)
        }
    }
}
