import Foundation

public struct OpenPetsPetReference: Codable, Equatable, Sendable {
    public enum Location: String, Codable, Sendable {
        case bundled
        case installed
    }

    public var id: String
    public var displayName: String
    public var directoryURL: URL
    public var location: Location

    public init(id: String, displayName: String, directoryURL: URL, location: Location) {
        self.id = id
        self.displayName = displayName
        self.directoryURL = directoryURL
        self.location = location
    }
}

public struct OpenPetsPetLibrary: Sendable {
    public var installedPetsDirectory: URL

    public init(installedPetsDirectory: URL = OpenPetsPaths.defaultInstalledPetsDirectory) {
        self.installedPetsDirectory = installedPetsDirectory
    }

    public func activePetURL(for configuration: OpenPetsConfiguration) -> URL {
        petURL(for: configuration.activePetID) ?? OpenPetsBundledPets.starcornURL
    }

    public func petURL(for id: String) -> URL? {
        if id == OpenPetsBundledPets.starcornID {
            return OpenPetsBundledPets.starcornURL
        }

        let installedURL = installedPetsDirectory.appendingPathComponent(id, isDirectory: true)
        guard FileManager.default.fileExists(atPath: installedURL.appendingPathComponent("pet.json").path) else {
            return nil
        }
        return installedURL
    }

    public func listPets() -> [OpenPetsPetReference] {
        var pets = [
            OpenPetsPetReference(
                id: OpenPetsBundledPets.starcornID,
                displayName: "Starcorn",
                directoryURL: OpenPetsBundledPets.starcornURL,
                location: .bundled
            )
        ]

        guard
            let children = try? FileManager.default.contentsOfDirectory(
                at: installedPetsDirectory,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
        else {
            return pets
        }

        for child in children.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            guard let bundle = try? PetBundle.load(from: child) else {
                continue
            }
            pets.append(OpenPetsPetReference(
                id: bundle.manifest.id,
                displayName: bundle.manifest.displayName,
                directoryURL: child,
                location: .installed
            ))
        }

        return pets
    }
}
