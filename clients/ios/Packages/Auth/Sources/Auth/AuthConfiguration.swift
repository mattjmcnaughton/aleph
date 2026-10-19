import Foundation

/// Where and how to sign in (iOS TDD §5.1). The App target reads it from Info.plist keys that
/// each build configuration sets: Debug uses the dev Keycloak `aleph-ios` client, Release the
/// Auth0 Native application.
public struct AuthConfiguration: Sendable {
    /// The redirect URI registered on both providers (PRD D14).
    public static let redirectURL = URL(string: "aleph://oauth/callback")!

    /// `openid` is load-bearing: the server resolves identity from userinfo (PRD §5.1).
    public static let scopes = ["openid", "profile", "email", "offline_access"]

    /// The OIDC issuer; endpoints come from its discovery document. `nil` when unconfigured.
    public var issuer: URL?
    /// The public client's id. No client secret ships in the binary.
    public var clientID: String
    /// Auth0's API Identifier (`OIDC_API_AUDIENCE` on the server). `nil` for Keycloak, whose
    /// `aleph-ios` client adds `aud=aleph-api` itself.
    public var audience: String?

    public init(issuer: URL?, clientID: String, audience: String?) {
        self.issuer = issuer
        self.clientID = clientID
        self.audience = audience
    }

    /// The sign-in request's extra parameters.
    var additionalParameters: [String: String]? {
        audience.map { ["audience": $0] }
    }
}
