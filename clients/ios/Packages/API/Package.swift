// swift-tools-version: 6.2
// The generated API client and (from AL-603) its adapters, middleware and SSE path
// (iOS TDD §3.3, §4.2). Sources/API/Generated/ is written by
// clients/ios/scripts/generate-api.sh and committed; never edit it by hand.
import PackageDescription

let package = Package(
    name: "API",
    platforms: [.iOS(.v26), .macOS(.v26)],
    products: [
        .library(name: "API", targets: ["API"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-runtime", exact: "1.13.2"),
        .package(url: "https://github.com/apple/swift-openapi-urlsession", exact: "1.3.2"),
    ],
    targets: [
        .target(
            name: "API",
            dependencies: [
                .product(name: "OpenAPIRuntime", package: "swift-openapi-runtime"),
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ]
        ),
        .testTarget(
            name: "APITests",
            dependencies: [
                "API",
                .product(name: "OpenAPIURLSession", package: "swift-openapi-urlsession"),
            ]
        ),
    ]
)
