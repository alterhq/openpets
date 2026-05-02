// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenPets",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "OpenPets", targets: ["OpenPetsCore"]),
        .executable(name: "openpets", targets: ["OpenPetsCLI"])
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
        .testTarget(
            name: "OpenPetsTests",
            dependencies: ["OpenPetsCore"]
        )
    ]
)
