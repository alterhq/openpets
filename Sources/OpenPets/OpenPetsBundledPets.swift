import Foundation

public enum OpenPetsBundledPets {
    public static let starcornID = "starcorn"

    public static var starcornURL: URL {
        if let manifestURL = Bundle.module.url(forResource: "pet", withExtension: "json") {
            return manifestURL.deletingLastPathComponent()
        }

        return Bundle.module
            .url(forResource: "Pets/starcorn", withExtension: nil)
            ?? Bundle.module.resourceURL!
                .appendingPathComponent("Pets", isDirectory: true)
                .appendingPathComponent("starcorn", isDirectory: true)
    }
}
