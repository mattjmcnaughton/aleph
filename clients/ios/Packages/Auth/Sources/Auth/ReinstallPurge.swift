import Foundation

/// Keychain items survive app deletion, so a reinstall could find an old Account's tokens
/// (iOS TDD §1.B2, §5.2). On the first launch after an install, every token is deleted and a
/// marker is written to `UserDefaults`, which a reinstall does clear.
public enum ReinstallPurge {
    public static let markerKey = "aleph.installMarker"

    /// Purges if this is the first launch since the install.
    ///
    /// - Returns: whether this call purged. When the Keychain cannot be cleared (it is locked
    ///   before the first unlock), the marker is not written, so the next launch tries again.
    @discardableResult
    public static func runIfNeeded(defaults: UserDefaults, store: any TokenStore) -> Bool {
        guard !defaults.bool(forKey: markerKey) else {
            return false
        }
        do {
            try store.deleteAll()
        } catch {
            return false
        }
        defaults.set(true, forKey: markerKey)
        return true
    }
}
