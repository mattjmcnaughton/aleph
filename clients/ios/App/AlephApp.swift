import Core
import Features
import SwiftUI

/// The thin App target (iOS TDD §3.3): it builds `AppDependencies` once and hands them to
/// the root view. `init` does no network work and does not read the Keychain (TDD §1.B1,
/// §1.B7); the first read happens when the root view asks for the session.
@main
struct AlephApp: App {
    private let dependencies = AppDependencies.forLaunch()

    var body: some Scene {
        WindowGroup {
            AlephRootView(dependencies: dependencies)
        }
    }
}
