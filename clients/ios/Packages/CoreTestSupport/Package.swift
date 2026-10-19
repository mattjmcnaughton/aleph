// swift-tools-version: 6.2
// In-memory fakes of Core's service protocols and ManualClock, for every package's tests and
// the snapshot tests (iOS TDD §3.3, §16.1). Its own package, not a second product of Core:
// a target of Core inside a static test-support library would put a second copy of Core's
// types next to the app's dynamic Core, and Xcode refuses to build that.
import PackageDescription

let package = Package(
    name: "CoreTestSupport",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "CoreTestSupport", targets: ["CoreTestSupport"])
    ],
    dependencies: [
        .package(path: "../Core")
    ],
    targets: [
        .target(
            name: "CoreTestSupport",
            dependencies: [.product(name: "Core", package: "Core")]
        ),
        .testTarget(
            name: "CoreTestSupportTests",
            dependencies: [
                "CoreTestSupport",
                .product(name: "Core", package: "Core"),
            ]
        ),
    ]
)
