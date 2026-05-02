import Foundation

public struct OpenPetsConfiguration: Codable, Equatable, Sendable {
    public var display: OpenPetsDisplayConfiguration
    public var socketPath: String

    public init(
        display: OpenPetsDisplayConfiguration = .default,
        socketPath: String = OpenPetsPaths.defaultSocketPath
    ) {
        self.display = display
        self.socketPath = socketPath
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
