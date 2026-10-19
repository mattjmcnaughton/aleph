import Foundation
import Security

/// Where the encoded `OIDAuthState` lives between launches.
public protocol TokenStore: Sendable {
    func read() throws -> Data?
    func write(_ data: Data) throws
    func deleteAll() throws
}

/// A `SecItem` call failed.
public struct KeychainError: Error, Equatable {
    public let status: OSStatus

    public init(status: OSStatus) {
        self.status = status
    }
}

/// One generic-password item, service `<bundle id>.auth` (iOS TDD §5.2), readable after the
/// first unlock and never synced or backed up off this device (TDD §1.B2): `WhenUnlocked`
/// would break a prewarmed launch and background stream completion.
public struct KeychainTokenStore: TokenStore {
    public let service: String
    private static let account = "auth-state"

    public init(service: String) {
        self.service = service
    }

    public func read() throws -> Data? {
        var query = itemQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        switch status {
        case errSecSuccess:
            return item as? Data
        case errSecItemNotFound:
            return nil
        default:
            throw KeychainError(status: status)
        }
    }

    public func write(_ data: Data) throws {
        let update: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        let status = SecItemUpdate(itemQuery as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var add = itemQuery
            add.merge(update) { _, new in new }
            let added = SecItemAdd(add as CFDictionary, nil)
            guard added == errSecSuccess else {
                throw KeychainError(status: added)
            }
        } else if status != errSecSuccess {
            throw KeychainError(status: status)
        }
    }

    /// Deletes every item for the service, not just the one this store writes.
    public func deleteAll() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError(status: status)
        }
    }

    private var itemQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: Self.account,
        ]
    }
}
