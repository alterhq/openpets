// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenPets",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "OpenPets", targets: ["OpenPetsCore"]),
        .executable(name: "openpets", targets: ["OpenPetsCLI"]),
        .executable(name: "openpets-menubar", targets: ["OpenPetsMenuBar"])
    ],
    dependencies: [
        .package(url: "https://github.com/modelcontextprotocol/swift-sdk.git", from: "0.12.0"),
        .package(url: "https://github.com/apple/swift-log.git", from: "1.5.0"),
        .package(url: "https://github.com/apple/swift-nio.git", from: "2.65.0"),
        .package(url: "https://github.com/sparkle-project/Sparkle.git", from: "2.6.0")
    ],
    targets: [
        .target(
            name: "OpenPetsCore",
            path: "Sources/OpenPets",
            resources: [
                .process("Resources")
            ]
        ),
        .executableTarget(
            name: "OpenPetsCLI",
            dependencies: ["OpenPetsCore"],
            path: "Sources/OpenPetsCLI"
        ),
        .executableTarget(
            name: "OpenPetsMenuBar",
            dependencies: [
                "OpenPetsCore",
                .product(name: "MCP", package: "swift-sdk"),
                .product(name: "Logging", package: "swift-log"),
                .product(name: "NIOCore", package: "swift-nio"),
                .product(name: "NIOHTTP1", package: "swift-nio"),
                .product(name: "NIOPosix", package: "swift-nio"),
                .product(name: "Sparkle", package: "Sparkle")
            ],
            path: "Sources/OpenPetsMenuBar"
        ),
        .testTarget(
            name: "OpenPetsTests",
            dependencies: [
                "OpenPetsCore",
                "OpenPetsMenuBar",
                .product(name: "MCP", package: "swift-sdk")
            ]
        )
    ]
)
