// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "OpenPets",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "OpenPets", targets: ["OpenPets"]),
        .executable(name: "openpets", targets: ["OpenPetsCLI"])
    ],
    targets: [
        .target(
            name: "OpenPets",
            path: "Sources/OpenPets"
        ),
        .executableTarget(
            name: "OpenPetsCLI",
            dependencies: ["OpenPets"],
            path: "Sources/OpenPetsCLI"
        ),
        .testTarget(
            name: "OpenPetsTests",
            dependencies: ["OpenPets"]
        )
    ]
)
