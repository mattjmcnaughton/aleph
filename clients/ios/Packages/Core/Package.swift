// swift-tools-version: 6.2
// App-owned models, the service protocols and pure rules (iOS TDD §3.3). No dependencies,
// nonisolated, Sendable; builds and tests on Linux (the swift-linux job). The in-memory fakes
// and ManualClock are the sibling package CoreTestSupport.
import PackageDescription

let package = Package(
    name: "Core",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        // Dynamic: the app and the app-hosted snapshot tests both link it, and two static
        // copies would make the same type two types at runtime (a thrown error's cast fails).
        .library(name: "Core", type: .dynamic, targets: ["Core"])
    ],
    targets: [
        .target(name: "Core"),
        .testTarget(name: "CoreTests", dependencies: ["Core"]),
    ]
)
