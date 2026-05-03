import Foundation

public struct OpenPetsConfiguration: Codable, Equatable, Sendable {
    public var display: OpenPetsDisplayConfiguration
    public var socketPath: String
    public var mcpHost: String
    public var mcpPort: Int
    public var mcpEndpoint: String
    public var activePetID: String

    public init(
        display: OpenPetsDisplayConfiguration = .default,
        socketPath: String = OpenPetsPaths.defaultSocketPath,
        mcpHost: String = "127.0.0.1",
        mcpPort: Int = 3001,
        mcpEndpoint: String = "/mcp",
        activePetID: String = OpenPetsBundledPets.starcornID
    ) {
        self.display = display
        self.socketPath = socketPath
        self.mcpHost = mcpHost
        self.mcpPort = mcpPort
        self.mcpEndpoint = mcpEndpoint
        self.activePetID = activePetID
    }

    private enum CodingKeys: String, CodingKey {
        case display
        case socketPath
        case mcpHost
        case mcpPort
        case mcpEndpoint
        case activePetID
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        display = try container.decodeIfPresent(OpenPetsDisplayConfiguration.self, forKey: .display) ?? .default
        socketPath = try container.decodeIfPresent(String.self, forKey: .socketPath) ?? OpenPetsPaths.defaultSocketPath
        mcpHost = try container.decodeIfPresent(String.self, forKey: .mcpHost) ?? "127.0.0.1"
        mcpPort = try container.decodeIfPresent(Int.self, forKey: .mcpPort) ?? 3001
        mcpEndpoint = try container.decodeIfPresent(String.self, forKey: .mcpEndpoint) ?? "/mcp"
        activePetID = try container.decodeIfPresent(String.self, forKey: .activePetID) ?? OpenPetsBundledPets.starcornID
    }

    public static func load(
        from url: URL = OpenPetsPaths.defaultConfigurationFileURL
    ) throws -> OpenPetsConfiguration {
        guard FileManager.default.fileExists(atPath: url.path) else {
            return OpenPetsConfiguration()
        }

        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(OpenPetsConfiguration.self, from: data)
    }

    @discardableResult
    public static func loadOrCreateDefault(
        at url: URL = OpenPetsPaths.defaultConfigurationFileURL
    ) throws -> OpenPetsConfiguration {
        if FileManager.default.fileExists(atPath: url.path) {
            return try load(from: url)
        }

        let configuration = OpenPetsConfiguration()
        try configuration.save(to: url)
        return configuration
    }

    public func save(to url: URL = OpenPetsPaths.defaultConfigurationFileURL) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(self)
        try data.write(to: url, options: .atomic)
    }
}
