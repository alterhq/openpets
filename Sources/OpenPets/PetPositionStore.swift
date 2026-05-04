import CoreGraphics
import Foundation

struct StoredPetPosition: Codable, Equatable {
    var x: Double
    var y: Double
    var kind: PetPositionKind

    init(_ point: CGPoint, kind: PetPositionKind = .petAnchor) {
        x = point.x
        y = point.y
        self.kind = kind
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        x = try container.decode(Double.self, forKey: .x)
        y = try container.decode(Double.self, forKey: .y)
        kind = try container.decodeIfPresent(PetPositionKind.self, forKey: .kind) ?? .windowOrigin
    }

    var point: CGPoint {
        CGPoint(x: x, y: y)
    }
}

enum PetPositionKind: String, Codable {
    case windowOrigin
    case petAnchor
}

final class PetPositionStore {
    private let url: URL

    init(url: URL = OpenPetsPaths.defaultApplicationSupportDirectory.appendingPathComponent("positions.json")) {
        self.url = url
    }

    func loadPosition(forPetID petID: String) -> CGPoint? {
        loadAll()[petID]?.point
    }

    func loadStoredPosition(forPetID petID: String) -> StoredPetPosition? {
        loadAll()[petID]
    }

    func savePosition(_ point: CGPoint, forPetID petID: String) throws {
        try savePosition(point, kind: .petAnchor, forPetID: petID)
    }

    func savePosition(_ point: CGPoint, kind: PetPositionKind, forPetID petID: String) throws {
        var positions = loadAll()
        positions[petID] = StoredPetPosition(point, kind: kind)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try JSONEncoder().encode(positions)
        try data.write(to: url, options: .atomic)
    }

    private func loadAll() -> [String: StoredPetPosition] {
        guard let data = try? Data(contentsOf: url) else {
            return [:]
        }
        return (try? JSONDecoder().decode([String: StoredPetPosition].self, from: data)) ?? [:]
    }
}
