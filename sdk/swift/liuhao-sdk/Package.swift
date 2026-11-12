// swift-tools-version: 5.9
// The swift-tools-version declares the minimum version of Swift required to build this package.

import PackageDescription

let package = Package(
    name: "LiuHaoSDK",
    platforms: [
        .iOS(.v15),
        .macOS(.v12),
        .tvOS(.v15),
        .watchOS(.v8),
        .visionOS(.v1)
    ],
    products: [
        // The main LiuHao AI OS SDK library.
        .library(
            name: "LiuHaoSDK",
            targets: ["LiuHaoSDK"]
        )
    ],
    dependencies: [
        // No external dependencies — the SDK is built entirely on
        // Foundation / FoundationNetworking for maximum portability.
    ],
    targets: [
        .target(
            name: "LiuHaoSDK",
            dependencies: [],
            path: "Sources/LiuHaoSDK"
        ),
        .testTarget(
            name: "LiuHaoSDKTests",
            dependencies: ["LiuHaoSDK"],
            path: "Tests/LiuHaoSDKTests"
        )
    ],
    swiftLanguageVersions: [.v5]
)
