import Core
import Testing

/// Mirrors `clients/web/src/lib/feature-flags.test.tsx` (`useFeatureFlag`): on, off, a key
/// the session never sent, and before the session has settled (iOS TDD §1.B11, PRD §5.9).
struct FeatureFlagsTests {
    @Test func offWhenTheSessionReportsTheFlagOff() {
        let flags = FeatureFlags(values: ["tutor": false])
        #expect(!flags.isOn(.tutor))
    }

    @Test func onWhenTheSessionEnablesTheFlag() {
        let flags = FeatureFlags(values: ["tutor": true])
        #expect(flags.isOn(.tutor))
    }

    @Test func offForAKeyTheSessionNeverSent() {
        let flags = FeatureFlags(values: ["tutor": true])
        #expect(!flags.isOn(.flow))
    }

    @Test(arguments: FeatureFlag.allCases)
    func everyFlagIsOffBeforeTheSessionSettles(flag: FeatureFlag) {
        #expect(!FeatureFlags.allOff.isOn(flag))
    }

    @Test func keysTheAppDoesNotKnowAreIgnored() {
        let flags = FeatureFlags(values: ["not_a_flag": true])
        #expect(FeatureFlag.allCases.allSatisfy { !flags.isOn($0) })
    }

    /// The five keys the web honours, spelled as the API spells them.
    @Test func theKeysMatchTheWebs() {
        #expect(FeatureFlag.allCases.map(\.rawValue) == ["tutor", "shaping", "streaks", "analyst", "flow"])
    }
}
