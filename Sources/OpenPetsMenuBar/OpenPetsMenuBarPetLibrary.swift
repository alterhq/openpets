import Foundation
import OpenPetsKit

struct OpenPetsMenuBarPetLibrary {
    private static let bundledPetBundleName = "OpenPetsKit_OpenPetsKit.bundle"
    private static let bundledStarcornID = "starcorn"

    var installedPetsDirectory: URL
    var discoveredPetsDirectories: [URL]
    var bundle: Bundle
    var fileManager: FileManager

    init(
        installedPetsDirectory: URL = OpenPetsPaths.defaultInstalledPetsDirectory,
        discoveredPetsDirectories: [URL] = OpenPetsPaths.defaultDiscoveredPetsDirectories,
        bundle: Bundle = .main,
        fileManager: FileManager = .default
    ) {
        self.installedPetsDirectory = installedPetsDirectory
        self.discoveredPetsDirectories = discoveredPetsDirectories
        self.bundle = bundle
        self.fileManager = fileManager
    }

    func activePetURL(for configuration: OpenPetsConfiguration) -> URL {
        petURL(for: configuration.activePetID) ?? bundledStarcornURL()
    }

    func petURL(for petID: String) -> URL? {
        if petID == Self.bundledStarcornID {
            return bundledStarcornURL()
        }

        let installedURL = installedPetsDirectory.appendingPathComponent(petID, isDirectory: true)
        guard !hasManifest(at: installedURL) else {
            return installedURL
        }

        if let installedBundleURL = petBundleURL(for: petID, in: [installedPetsDirectory]) {
            return installedBundleURL
        }
        return discoveredPetURL(for: petID)
    }

    func listPets() -> [OpenPetsPetReference] {
        var pets = [
            OpenPetsPetReference(
                id: Self.bundledStarcornID,
                displayName: "Starcorn",
                directoryURL: bundledStarcornURL(),
                location: .bundled
            )
        ]
        var seenPetIDs = Set([Self.bundledStarcornID])

        for bundleURL in petBundleURLs(in: installedPetsDirectory) {
            guard
                let manifest = loadDiscoverableManifest(from: bundleURL),
                seenPetIDs.insert(manifest.id).inserted
            else {
                continue
            }
            pets.append(OpenPetsPetReference(
                id: manifest.id,
                displayName: manifest.displayName,
                directoryURL: bundleURL,
                location: .installed
            ))
        }

        for directory in discoveredPetsDirectories {
            for bundleURL in petBundleURLs(in: directory) {
                guard
                    let manifest = loadDiscoverableManifest(from: bundleURL),
                    seenPetIDs.insert(manifest.id).inserted
                else {
                    continue
                }
                pets.append(OpenPetsPetReference(
                    id: manifest.id,
                    displayName: manifest.displayName,
                    directoryURL: bundleURL,
                    location: .installed
                ))
            }
        }

        return pets
    }

    func bundledStarcornURL() -> URL {
        for resourceBundleURL in Self.resourceBundleURLs(bundle: bundle) {
            if hasManifest(at: resourceBundleURL) {
                return resourceBundleURL
            }

            let nestedResourceURL = resourceBundleURL
                .appendingPathComponent("Pets", isDirectory: true)
                .appendingPathComponent(Self.bundledStarcornID, isDirectory: true)
            if hasManifest(at: nestedResourceURL) {
                return nestedResourceURL
            }
        }

        return sourceStarcornURL()
    }

    static func resourceBundleURLs(bundle: Bundle = .main) -> [URL] {
        var candidates: [URL] = []

        if let resourceURL = bundle.resourceURL {
            candidates.append(resourceURL.appendingPathComponent(bundledPetBundleName, isDirectory: true))
        }
        candidates.append(bundle.bundleURL.appendingPathComponent(bundledPetBundleName, isDirectory: true))
        candidates.append(bundle.bundleURL.appendingPathComponent("Contents/Resources/\(bundledPetBundleName)", isDirectory: true))

        if let executableURL = bundle.executableURL {
            let executableDirectoryURL = executableURL.deletingLastPathComponent()
            candidates.append(executableDirectoryURL.appendingPathComponent(bundledPetBundleName, isDirectory: true))
            candidates.append(executableDirectoryURL.appendingPathComponent("../Resources/\(bundledPetBundleName)", isDirectory: true))

            var ancestorURL = executableDirectoryURL
            for _ in 0..<5 {
                ancestorURL.deleteLastPathComponent()
                candidates.append(ancestorURL.appendingPathComponent(bundledPetBundleName, isDirectory: true))
            }
        }

        return uniqueURLs(candidates)
    }

    private func discoveredPetURL(for petID: String) -> URL? {
        petBundleURL(for: petID, in: discoveredPetsDirectories)
    }

    private func petBundleURL(for petID: String, in directories: [URL]) -> URL? {
        for directory in directories {
            for bundleURL in petBundleURLs(in: directory) {
                guard let manifest = loadDiscoverableManifest(from: bundleURL), manifest.id == petID else {
                    continue
                }
                return bundleURL
            }
        }
        return nil
    }

    private func loadDiscoverableManifest(from bundleURL: URL) -> PetManifest? {
        guard let manifest = try? PetBundle.loadManifest(from: bundleURL) else {
            return nil
        }
        let spritesheetURL = bundleURL.appendingPathComponent(manifest.spritesheetPath)
        guard fileManager.fileExists(atPath: spritesheetURL.path) else {
            return nil
        }
        return manifest
    }

    private func petBundleURLs(in directory: URL) -> [URL] {
        var bundles: [URL] = []
        if hasManifest(at: directory) {
            bundles.append(directory)
        }

        guard
            let children = try? fileManager.contentsOfDirectory(
                at: directory,
                includingPropertiesForKeys: [.isDirectoryKey],
                options: [.skipsHiddenFiles]
            )
        else {
            return bundles
        }

        for child in children.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) where hasManifest(at: child) {
            bundles.append(child)
        }

        return bundles
    }

    private func hasManifest(at url: URL) -> Bool {
        fileManager.fileExists(atPath: url.appendingPathComponent("pet.json").path)
    }

    private func sourceStarcornURL() -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent(".build", isDirectory: true)
            .appendingPathComponent("checkouts", isDirectory: true)
            .appendingPathComponent("OpenPetsKit", isDirectory: true)
            .appendingPathComponent("Sources/OpenPetsKit/Resources/Pets/starcorn", isDirectory: true)
    }

    private static func uniqueURLs(_ urls: [URL]) -> [URL] {
        var seenPaths = Set<String>()
        return urls.filter { url in
            let standardizedURL = url.standardizedFileURL
            return seenPaths.insert(standardizedURL.path).inserted
        }
    }
}
