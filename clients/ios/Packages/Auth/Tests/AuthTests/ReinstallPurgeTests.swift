import Auth
import Foundation
import Testing

/// Keychain items survive app deletion; the first launch after an install purges them
/// (iOS TDD §1.B2, §5.2).
struct ReinstallPurgeTests {
    private func freshDefaults() throws -> UserDefaults {
        try #require(UserDefaults(suiteName: "aleph.tests.\(UUID().uuidString)"))
    }

    @Test func purgesOnTheFirstLaunchAfterAnInstall() throws {
        let defaults = try freshDefaults()
        let store = InMemoryTokenStore(Data("an old Account's session".utf8))

        let purged = ReinstallPurge.runIfNeeded(defaults: defaults, store: store)

        #expect(purged)
        #expect(store.data == nil)
        #expect(defaults.bool(forKey: ReinstallPurge.markerKey))
    }

    @Test func leavesTheSessionAloneOnLaterLaunches() throws {
        let defaults = try freshDefaults()
        let store = InMemoryTokenStore()
        ReinstallPurge.runIfNeeded(defaults: defaults, store: store)
        try store.write(Data("this install's session".utf8))

        let purged = ReinstallPurge.runIfNeeded(defaults: defaults, store: store)

        #expect(!purged)
        #expect(store.data == Data("this install's session".utf8))
    }

    @Test func aLockedKeychainIsPurgedOnTheNextLaunchInstead() throws {
        let defaults = try freshDefaults()
        let store = InMemoryTokenStore(Data("an old Account's session".utf8))
        store.setFailing(true)

        #expect(!ReinstallPurge.runIfNeeded(defaults: defaults, store: store))
        #expect(!defaults.bool(forKey: ReinstallPurge.markerKey))

        store.setFailing(false)
        #expect(ReinstallPurge.runIfNeeded(defaults: defaults, store: store))
        #expect(store.data == nil)
    }
}
