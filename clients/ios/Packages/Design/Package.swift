// swift-tools-version: 6.2
// Nocturne for SwiftUI (iOS TDD §3.3): colour and spacing tokens copied from
// clients/web/tailwind.config.ts, the shared components every screen uses, and (from
// AL-605) MarkdownView. Main-actor by default; iOS only.
import PackageDescription

let package = Package(
    name: "Design",
    defaultLocalization: "en",
    platforms: [.iOS(.v26)],
    products: [
        // Dynamic: the app and the app-hosted snapshot tests both link it, and two static
        // copies would make the same type two types at runtime (a thrown error's cast fails).
        .library(name: "Design", type: .dynamic, targets: ["Design"])
    ],
    dependencies: [
        .package(path: "../Core"),
        .package(path: "../Markdown"),
    ],
    targets: [
        .target(
            name: "Design",
            dependencies: [
                .product(name: "Core", package: "Core"),
                .product(name: "AlephMarkdown", package: "Markdown"),
            ],
            resources: [.process("Resources")],
            swiftSettings: [
                .defaultIsolation(MainActor.self),
                .enableUpcomingFeature("NonisolatedNonsendingByDefault"),
            ]
        )
    ]
)
