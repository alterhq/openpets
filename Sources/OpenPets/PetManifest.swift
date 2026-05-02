import Foundation

public struct PetManifest: Codable, Equatable, Sendable {
    public var id: String
    public var displayName: String
    public var description: String
    public var spritesheetPath: String

    public init(id: String, displayName: String, description: String, spritesheetPath: String) {
        self.id = id
        self.displayName = displayName
        self.description = description
        self.spritesheetPath = spritesheetPath
    }
}

public struct PetAtlas: Codable, Equatable, Sendable {
    public static let codexColumns = 8
    public static let codexRows = 9

    public var columns: Int
    public var rows: Int
    public var cellWidth: Int
    public var cellHeight: Int
    public var pixelWidth: Int
    public var pixelHeight: Int

    public init(columns: Int, rows: Int, cellWidth: Int, cellHeight: Int, pixelWidth: Int, pixelHeight: Int) {
        self.columns = columns
        self.rows = rows
        self.cellWidth = cellWidth
        self.cellHeight = cellHeight
        self.pixelWidth = pixelWidth
        self.pixelHeight = pixelHeight
    }
}
