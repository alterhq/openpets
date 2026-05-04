import Foundation

public enum OpenPetsBundledPets {
    public static let starcornID = "starcorn"

    public static var starcornURL: URL {
        bundledPetURL(named: "starcorn")
            ?? sourcePetURL(named: "starcorn")
    }

    private static func bundledPetURL(named petID: String) -> URL? {
        for resourceBundleURL in resourceBundleCandidates() {
            let processedResourceURL = resourceBundleURL
            if hasManifest(at: processedResourceURL) {
                return processedResourceURL
            }

            let nestedResourceURL = processedResourceURL
                .appendingPathComponent("Pets", isDirectory: true)
                .appendingPathComponent(petID, isDirectory: true)
            if hasManifest(at: nestedResourceURL) {
                return nestedResourceURL
            }
        }

        return nil
    }

    private static func resourceBundleCandidates() -> [URL] {
        let bundleName = "OpenPets_OpenPetsCore.bundle"
        var candidates: [URL] = []

        if let resourceURL = Bundle.main.resourceURL {
            candidates.append(resourceURL.appendingPathComponent(bundleName, isDirectory: true))
        }

        candidates.append(Bundle.main.bundleURL.appendingPathComponent(bundleName, isDirectory: true))
        candidates.append(Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent(bundleName, isDirectory: true))

        if let executableURL = Bundle.main.executableURL {
            candidates.append(executableURL.deletingLastPathComponent().appendingPathComponent(bundleName, isDirectory: true))
        }

        return uniqueExistingURLs(candidates)
    }

    private static func sourcePetURL(named petID: String) -> URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Resources", isDirectory: true)
            .appendingPathComponent("Pets", isDirectory: true)
            .appendingPathComponent(petID, isDirectory: true)
    }

    private static func hasManifest(at url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.appendingPathComponent("pet.json").path)
    }

    private static func uniqueExistingURLs(_ urls: [URL]) -> [URL] {
        var seen = Set<String>()
        return urls.compactMap { url in
            let standardizedURL = url.standardizedFileURL
            guard FileManager.default.fileExists(atPath: standardizedURL.path), seen.insert(standardizedURL.path).inserted else {
                return nil
            }
            return standardizedURL
        }
    }
}
