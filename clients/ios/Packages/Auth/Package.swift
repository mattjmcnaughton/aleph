// swift-tools-version: 6.2
// Sign-in and tokens (iOS TDD §3.3, §5): LiveAuthSession over AppAuth (pinned exactly, TDD
// D10), the Keychain token store and the reinstall purge. iOS only. Features cannot import
// this package or AppAuth: screens see only Core's AuthSession protocol.
import PackageDescription

let package = Package(
    name: "Auth",
    platforms: [.iOS(.v26)],
    products: [
        .library(name: "Auth", targets: ["Auth"])
    ],
    dependencies: [
        .package(path: "../Core"),
        .package(path: "../CoreTestSupport"),
        .package(url: "https://github.com/openid/AppAuth-iOS", exact: "3.0.0"),
    ],
    targets: [
        .target(
            name: "Auth",
            dependencies: [
                .product(name: "Core", package: "Core"),
                .product(name: "AppAuth", package: "AppAuth-iOS"),
            ]
        ),
        .testTarget(
            name: "AuthTests",
            dependencies: [
                "Auth",
                .product(name: "CoreTestSupport", package: "CoreTestSupport"),
            ]
        ),
    ]
)
