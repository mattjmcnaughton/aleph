// swift-tools-version: 6.2
// The screens and their models, the Router and the SessionStore (iOS TDD §3.3, §6). Main-actor
// by default; iOS only. The manifest is the layering rule: Features depends on Core and Design
// only, so it cannot import API, Auth, AppAuth or OpenAPIRuntime and never sees a generated type.
import PackageDescription

let uiSettings: [SwiftSetting] = [
    .defaultIsolation(MainActor.self),
    .enableUpcomingFeature("NonisolatedNonsendingByDefault"),
]

let package = Package(
    name: "Features",
    defaultLocalization: "en",
    platforms: [.iOS(.v26)],
    products: [
        // Dynamic: the app and the app-hosted snapshot tests both link it, and two static
        // copies would make the same type two types at runtime (a thrown error's cast fails).
        .library(name: "Features", type: .dynamic, targets: ["Features"])
    ],
    dependencies: [
        .package(path: "../Core"),
        .package(path: "../CoreTestSupport"),
        .package(path: "../Design"),
    ],
    targets: [
        .target(
            name: "Features",
            dependencies: [
                .product(name: "Core", package: "Core"),
                .product(name: "Design", package: "Design"),
            ],
            resources: [.process("Resources")],
            swiftSettings: uiSettings
        ),
        .testTarget(
            name: "FeaturesTests",
            dependencies: [
                "Features",
                .product(name: "CoreTestSupport", package: "CoreTestSupport"),
            ],
            swiftSettings: uiSettings
        ),
    ]
)
