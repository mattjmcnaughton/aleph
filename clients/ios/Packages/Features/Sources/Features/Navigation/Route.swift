import Core

/// A screen pushed onto the one `NavigationStack` rooted at Your paths (iOS TDD §3.5). Cases
/// carry typed ids, never URLs, so a link in model output can never drive navigation. Each
/// case gets its destination in the ticket that builds its screen.
nonisolated public enum Route: Hashable, Codable, Sendable {
    case path(PathID)
    case lesson(LessonID)
    case newPath(topic: String?)
    case beat(BeatID)
    case deployAnalyst(topic: String?)
    case brief(BriefID)
    case flowSetup(preselect: PathID?)
    case flowReceipt
}
