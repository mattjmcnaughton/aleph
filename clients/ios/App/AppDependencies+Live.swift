import API
import Auth
import Core
import Foundation

extension AppDependencies {
    /// What this launch runs on: the live services, or, in the UI-test build only, the
    /// pre-signed tokens the UI tests pass at launch (TDD §5.5).
    static func forLaunch(arguments: [String] = ProcessInfo.processInfo.arguments) -> AppDependencies {
        #if ALEPH_UI_TESTING
            if let tokens = UITestAuthSession.tokens(fromLaunchArguments: arguments) {
                return live(auth: UITestAuthSession(tokens: tokens))
            }
        #endif
        let configuration = AppConfiguration(bundle: .main)
        let auth = LiveAuthSession(
            configuration: configuration.auth,
            store: KeychainTokenStore(service: "\(configuration.bundleIdentifier).auth")
        )
        return live(auth: auth, configuration: configuration)
    }

    static func live(
        auth: any AuthSession,
        configuration: AppConfiguration = AppConfiguration(bundle: .main)
    ) -> AppDependencies {
        AppDependencies(
            auth: auth,
            session: LiveSessionAPI(serverURL: configuration.apiBaseURL, tokens: auth),
            clock: ContinuousClock(),
            now: { Date() }
        )
    }
}

/// Per-build-configuration settings, from the Info.plist keys `project.yml` fills in
/// (Debug: the local backend and dev Keycloak; UITest: the e2e stub backend; Release:
/// production and Auth0).
struct AppConfiguration {
    let bundleIdentifier: String
    let apiBaseURL: URL
    let auth: AuthConfiguration

    init(bundle: Bundle) {
        func value(_ key: String) -> String? {
            guard let value = bundle.object(forInfoDictionaryKey: key) as? String, !value.isEmpty else {
                return nil
            }
            return value
        }
        bundleIdentifier = bundle.bundleIdentifier ?? "com.mattjmcnaughton.aleph.app"
        guard let apiBaseURL = value("AlephAPIBaseURL").flatMap({ URL(string: $0) }) else {
            preconditionFailure("AlephAPIBaseURL is missing from Info.plist")
        }
        self.apiBaseURL = apiBaseURL
        auth = AuthConfiguration(
            issuer: value("AlephOIDCIssuer").flatMap { URL(string: $0) },
            clientID: value("AlephOIDCClientID") ?? "",
            audience: value("AlephOIDCAudience")
        )
    }
}
