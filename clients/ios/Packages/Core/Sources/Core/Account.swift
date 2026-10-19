/// The signed-in learner's Account, as `GET /api/v1/auth/session` reports it (PRD §5.1). The
/// app treats this, not the token, as the signed-in state, exactly as the web's root
/// `beforeLoad` does.
public struct Account: Equatable, Sendable {
    public var id: String
    public var username: String
    public var displayName: String
    public var email: String?
    public var isAdmin: Bool
    public var featureFlags: FeatureFlags

    public init(
        id: String,
        username: String,
        displayName: String,
        email: String?,
        isAdmin: Bool,
        featureFlags: FeatureFlags
    ) {
        self.id = id
        self.username = username
        self.displayName = displayName
        self.email = email
        self.isAdmin = isAdmin
        self.featureFlags = featureFlags
    }

    /// The name the home greeting uses: the display name up to its first space. Web:
    /// `display_name.split(" ")[0]` in `routes/index.tsx`.
    public var firstName: String {
        String(displayName.split(separator: " ", omittingEmptySubsequences: false).first ?? "")
    }
}
