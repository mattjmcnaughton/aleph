// swift-tools-version: 6.2
// The tools clients/ios/scripts/generate-api.sh runs (iOS TDD §4.2):
// - prepare-contract: rewrites a temp copy of clients/openapi.json into the
//   nullability form swift-openapi-generator 1.x supports (see ContractRewrite).
// - swift-openapi-generator: the generator CLI, pinned exactly so every machine
//   generates the same Swift. Bump it deliberately, then regenerate and commit.
import PackageDescription

let package = Package(
    name: "GenerateAPI",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "prepare-contract", targets: ["PrepareContract"])
    ],
    dependencies: [
        .package(url: "https://github.com/apple/swift-openapi-generator", exact: "1.14.2")
    ],
    targets: [
        .target(name: "ContractRewrite"),
        .executableTarget(name: "PrepareContract", dependencies: ["ContractRewrite"]),
        .testTarget(name: "ContractRewriteTests", dependencies: ["ContractRewrite"]),
    ]
)
