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
    public var discoveredPetsDirectories: [URL]

    public init(
        installedPetsDirectory: URL = OpenPetsPaths.defaultInstalledPetsDirectory,
        discoveredPetsDirectories: [URL] = OpenPetsPaths.defaultDiscoveredPetsDirectories
    ) {
        self.installedPetsDirectory = installedPetsDirectory
        self.discoveredPetsDirectories = discoveredPetsDirectories
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
            if let installedBundleURL = petBundleURL(for: id, in: [installedPetsDirectory]) {
                return installedBundleURL
            }
            return discoveredPetURL(for: id)
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
        var seenPetIDs = Set([OpenPetsBundledPets.starcornID])

        for bundleURL in petBundleURLs(in: installedPetsDirectory) {
            guard let bundle = try? PetBundle.load(from: bundleURL), seenPetIDs.insert(bundle.manifest.id).inserted else {
                continue
            }
            pets.append(OpenPetsPetReference(
                id: bundle.manifest.id,
                displayName: bundle.manifest.displayName,
                directoryURL: bundleURL,
                location: .installed
            ))
        }

        for directory in discoveredPetsDirectories {
            for bundleURL in petBundleURLs(in: directory) {
                guard let bundle = try? PetBundle.load(from: bundleURL), seenPetIDs.insert(bundle.manifest.id).inserted else {
                    continue
                }
                pets.append(OpenPetsPetReference(
                    id: bundle.manifest.id,
                    displayName: bundle.manifest.displayName,
                    directoryURL: bundleURL,
                    location: .installed
                ))
            }
        }

        return pets
    }

    private func discoveredPetURL(for id: String) -> URL? {
        petBundleURL(for: id, in: discoveredPetsDirectories)
    }

    private func petBundleURL(for id: String, in directories: [URL]) -> URL? {
        for directory in directories {
            for bundleURL in petBundleURLs(in: directory) {
                guard let bundle = try? PetBundle.load(from: bundleURL), bundle.manifest.id == id else {
                    continue
                }
                return bundleURL
            }
        }
        return nil
    }

    private func petBundleURLs(in directory: URL) -> [URL] {
        var bundles: [URL] = []
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("pet.json").path) {
            bundles.append(directory)
        }

        guard
            let children = try? FileManager.default.contentsOfDirectory(
                at: directory,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
        else {
            return bundles
        }

        for child in children.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            guard FileManager.default.fileExists(atPath: child.appendingPathComponent("pet.json").path) else {
                continue
            }
            bundles.append(child)
        }

        return bundles
    }
}
