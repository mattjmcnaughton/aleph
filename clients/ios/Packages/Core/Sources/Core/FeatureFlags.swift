/// The feature-flag keys both clients honour (PRD §5.9). No iOS-only flag exists (TDD D13).
public enum FeatureFlag: String, CaseIterable, Sendable {
    case tutor
    case shaping
    case streaks
    case analyst
    case flow
}

/// `user.feature_flags` from the session probe (TDD §1.B11). A key the server did not send
/// reads as off, and so does every key before the probe lands, so a gate never flashes open.
/// Web: `useFeatureFlag` (`clients/web/src/lib/feature-flags.ts`).
public struct FeatureFlags: Equatable, Sendable {
    /// Before the session probe has answered: everything off.
    public static let allOff = FeatureFlags(values: [:])

    private let values: [String: Bool]

    /// - Parameter values: the server's map, keys as the API spells them. Keys this app does
    ///   not know are kept but never read.
    public init(values: [String: Bool]) {
        self.values = values
    }

    public func isOn(_ flag: FeatureFlag) -> Bool {
        values[flag.rawValue] ?? false
    }
}
