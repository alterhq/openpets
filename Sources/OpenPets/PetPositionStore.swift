import CoreGraphics
import Foundation

struct StoredPetPosition: Codable, Equatable {
    var x: Double
    var y: Double

    init(_ point: CGPoint) {
        x = point.x
        y = point.y
    }

    var point: CGPoint {
        CGPoint(x: x, y: y)
    }
}

final class PetPositionStore {
    private let url: URL

    init(url: URL = OpenPetsPaths.defaultApplicationSupportDirectory.appendingPathComponent("positions.json")) {
        self.url = url
    }

    func loadPosition(forPetID petID: String) -> CGPoint? {
        loadAll()[petID]?.point
    }

    func savePosition(_ point: CGPoint, forPetID petID: String) throws {
        var positions = loadAll()
        positions[petID] = StoredPetPosition(point)
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
