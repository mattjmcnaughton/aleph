import Foundation

/// The app's collaborators (iOS TDD §3.4): built once by the App target, published through
/// the SwiftUI environment, and handed to each screen model through its initialiser. Models
/// never read the environment themselves, so they test without SwiftUI. Later tickets add
/// the path, lesson, progress, Beat, Brief, tutor, shaping, device and preference services.
public struct AppDependencies: Sendable {
    public var auth: any AuthSession
    public var session: any SessionAPI
    public var clock: any Clock<Duration>
    public var now: @Sendable () -> Date

    public init(
        auth: any AuthSession,
        session: any SessionAPI,
        clock: any Clock<Duration>,
        now: @escaping @Sendable () -> Date
    ) {
        self.auth = auth
        self.session = session
        self.clock = clock
        self.now = now
    }
}
