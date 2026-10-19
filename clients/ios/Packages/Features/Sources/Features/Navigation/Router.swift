import Observation

/// The navigation stack, as an array the app owns (iOS TDD §1.A5, §3.5): push is `append`,
/// back is the system's edge swipe.
@Observable
public final class Router {
    public var stack: [Route] = []

    public init() {}

    public func push(_ route: Route) {
        stack.append(route)
    }

    public func popToRoot() {
        stack.removeAll()
    }

    /// A push tap or a path-complete door replaces the whole stack.
    public func replace(with routes: [Route]) {
        stack = routes
    }
}
