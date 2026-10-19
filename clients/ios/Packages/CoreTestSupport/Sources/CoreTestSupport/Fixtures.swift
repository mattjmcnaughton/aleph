import Core
import Foundation

extension Account {
    /// A plain learner, as the web's MSW `authenticatedSession` (`clients/web/src/mocks/handlers.ts`).
    public static let learner = Account(
        id: "11111111-1111-4111-8111-111111111111",
        username: "dev",
        displayName: "Dev Learner",
        email: "dev@example.com",
        isAdmin: false,
        featureFlags: .allOff
    )

    /// The learner with these flags.
    public static func learner(flags: [String: Bool]) -> Account {
        var account = Account.learner
        account.featureFlags = FeatureFlags(values: flags)
        return account
    }
}

extension AppDependencies {
    /// A fixed instant for tests and snapshots: 2026-01-01T00:00:00Z.
    public static let fixedNow = Date(timeIntervalSince1970: 1_767_225_600)

    /// Dependencies built from in-memory fakes (iOS TDD §3.4).
    public static func fake(
        auth: any AuthSession = FakeAuthSession(),
        session: any SessionAPI = InMemorySessionAPI(),
        clock: ManualClock = ManualClock()
    ) -> AppDependencies {
        AppDependencies(auth: auth, session: session, clock: clock, now: { fixedNow })
    }
}
