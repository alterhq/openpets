// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenPets",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .executable(name: "openpets", targets: ["OpenPetsCLI"]),
        .executable(name: "openpets-menubar", targets: ["OpenPetsMenuBar"])
    ],
    dependencies: [
        .package(url: "https://github.com/alterhq/OpenPetsKit.git", from: "0.2.1"),
        .package(url: "https://github.com/modelcontextprotocol/swift-sdk.git", from: "0.12.0"),
        .package(url: "https://github.com/apple/swift-log.git", from: "1.12.0"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.99.0"),
        .package(url: "https://github.com/sparkle-project/Sparkle.git", from: "2.9.1")
    ],
    targets: [
        .executableTarget(
            name: "OpenPetsCLI",
            dependencies: [
                .product(name: "OpenPetsKit", package: "OpenPetsKit")
            ],
            path: "Sources/OpenPetsCLI"
        ),
        .executableTarget(
            name: "OpenPetsMenuBar",
            dependencies: [
                .product(name: "OpenPetsKit", package: "OpenPetsKit"),
                .product(name: "MCP", package: "swift-sdk"),
                .product(name: "Logging", package: "swift-log"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOHTTP1", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "Sparkle", package: "Sparkle")
            ],
            path: "Sources/OpenPetsMenuBar",
            resources: [
                .process("Resources")
            ]
        ),
        .testTarget(
            name: "OpenPetsTests",
            dependencies: [
                .product(name: "OpenPetsKit", package: "OpenPetsKit"),
                "OpenPetsMenuBar",
                .product(name: "MCP", package: "swift-sdk")
            ]
        )
    ]
)
