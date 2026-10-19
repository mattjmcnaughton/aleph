import Core
import Design
import SwiftUI

/// The app's root (iOS TDD §3.4, §3.5): the launch screen, sign-in, or the one
/// `NavigationStack` rooted at Your paths, by `SessionStore.state`. Re-reads the session on
/// every return to `.active` (TDD §1.B1).
public struct AlephRootView: View {
    @State private var store: SessionStore
    @State private var router = Router()
    @Environment(\.scenePhase) private var scenePhase

    private let dependencies: AppDependencies

    public init(dependencies: AppDependencies) {
        self.dependencies = dependencies
        _store = State(initialValue: SessionStore(auth: dependencies.auth, session: dependencies.session))
    }

    public var body: some View {
        content
            .preferredColorScheme(.dark)
            .task {
                await store.start()
            }
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active else {
                    return
                }
                Task { await store.sceneBecameActive() }
            }
    }

    @ViewBuilder private var content: some View {
        switch store.state {
        case .launching:
            LaunchView()
        case .signedOut:
            SignInScreen(model: SignInModel(auth: dependencies.auth) { await store.didSignIn() })
        case .signedIn(let account):
            NavigationStack(path: $router.stack) {
                HomeScreen(account: account, onSignOut: signOut)
            }
        case .unavailable(let error):
            UnavailableView(
                error: error,
                retry: { Task { await store.retry() } },
                signOut: signOut
            )
        }
    }
}

extension AlephRootView {
    /// Signing out also empties the navigation stack, so the next Account starts at its root.
    private func signOut() {
        router.popToRoot()
        Task { await store.signOut() }
    }
}

/// The first frame: the night background and the mark, matching the launch screen.
struct LaunchView: View {
    var body: some View {
        ZStack {
            Palette.night.ignoresSafeArea()
            AlephMark()
        }
        .accessibilityIdentifier("launch")
    }
}

/// Signed in, but the session probe failed: say so, and offer Retry (PRD §5 cross-cutting
/// rules) and a way out.
struct UnavailableView: View {
    let error: AppError
    let retry: () -> Void
    let signOut: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: Spacing.lg) {
                StateCard.error(
                    error,
                    title: String(localized: "We couldn't load Aleph.", bundle: .module),
                    retry: retry
                )
                Button(action: signOut) {
                    Text("Sign out", bundle: .module)
                }
                .foregroundStyle(Palette.mist)
            }
            .padding(Spacing.lg)
            .padding(.top, Spacing.xxl)
        }
        .background(Palette.night)
        .accessibilityIdentifier("session-unavailable")
    }
}
