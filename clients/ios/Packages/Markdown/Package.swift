// swift-tools-version: 6.2
// Markdown for Read passages and tutor replies (iOS TDD §3.3, §7, ADR 0002). Nonisolated,
// Sendable, no UI; builds and tests on Linux. AL-605 adds swift-markdown and the allowlist
// walker into MarkdownDocument. The module is AlephMarkdown, not Markdown, because
// swift-markdown's own module is called Markdown and SwiftPM forbids two targets of one name.
import PackageDescription

let package = Package(
    name: "Markdown",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "AlephMarkdown", targets: ["AlephMarkdown"])
    ],
    targets: [
        .target(name: "AlephMarkdown"),
        .testTarget(name: "AlephMarkdownTests", dependencies: ["AlephMarkdown"]),
    ]
)
