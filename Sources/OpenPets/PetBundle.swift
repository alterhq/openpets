import CoreGraphics
import Foundation
import ImageIO

public enum OpenPetsError: Error, LocalizedError, Equatable, Sendable {
    case missingManifest(URL)
    case missingSpritesheet(URL)
    case invalidSpritesheet(URL)
    case invalidAtlasDimensions(width: Int, height: Int)
    case invalidSocketPath(String)
    case socketAlreadyInUse(String)
    case socketFailure(String)
    case protocolFailure(String)

    public var errorDescription: String? {
        switch self {
        case .missingManifest(let url):
            "Missing pet manifest at \(url.path)"
        case .missingSpritesheet(let url):
            "Missing pet spritesheet at \(url.path)"
        case .invalidSpritesheet(let url):
            "Could not read pet spritesheet at \(url.path)"
        case .invalidAtlasDimensions(let width, let height):
            "Spritesheet dimensions \(width)x\(height) are not divisible into an 8x9 Codex pet atlas"
        case .invalidSocketPath(let path):
            "Unix socket path is too long or invalid: \(path)"
        case .socketAlreadyInUse(let path):
            "A pet host is already running on \(path)"
        case .socketFailure(let message):
            message
        case .protocolFailure(let message):
            message
        }
    }
}

public struct PetBundle: Sendable {
    public var directoryURL: URL
    public var manifest: PetManifest
    public var spritesheetURL: URL
    public var atlas: PetAtlas

    public init(directoryURL: URL, manifest: PetManifest, spritesheetURL: URL, atlas: PetAtlas) {
        self.directoryURL = directoryURL
        self.manifest = manifest
        self.spritesheetURL = spritesheetURL
        self.atlas = atlas
    }

    public static func load(from directoryURL: URL) throws -> PetBundle {
        let manifestURL = directoryURL.appendingPathComponent("pet.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path) else {
            throw OpenPetsError.missingManifest(manifestURL)
        }

        let manifest = try JSONDecoder().decode(PetManifest.self, from: Data(contentsOf: manifestURL))
        let spritesheetURL = directoryURL.appendingPathComponent(manifest.spritesheetPath)
        guard FileManager.default.fileExists(atPath: spritesheetURL.path) else {
            throw OpenPetsError.missingSpritesheet(spritesheetURL)
        }

        let atlas = try readAtlas(from: spritesheetURL)
        return PetBundle(
            directoryURL: directoryURL,
            manifest: manifest,
            spritesheetURL: spritesheetURL,
            atlas: atlas
        )
    }

    private static func readAtlas(from spritesheetURL: URL) throws -> PetAtlas {
        guard
            let source = CGImageSourceCreateWithURL(spritesheetURL as CFURL, nil),
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
            let width = properties[kCGImagePropertyPixelWidth] as? Int,
            let height = properties[kCGImagePropertyPixelHeight] as? Int
        else {
            throw OpenPetsError.invalidSpritesheet(spritesheetURL)
        }

        guard width % PetAtlas.codexColumns == 0, height % PetAtlas.codexRows == 0 else {
            throw OpenPetsError.invalidAtlasDimensions(width: width, height: height)
        }

        return PetAtlas(
            columns: PetAtlas.codexColumns,
            rows: PetAtlas.codexRows,
            cellWidth: width / PetAtlas.codexColumns,
            cellHeight: height / PetAtlas.codexRows,
            pixelWidth: width,
            pixelHeight: height
        )
    }
}
