import Darwin
import Foundation
import OpenPetsCore

protocol OpenPetsPortChecking {
    func isPortAvailable(host: String, port: Int) -> Bool
}

struct OpenPetsSystemPortChecker: OpenPetsPortChecking {
    func isPortAvailable(host: String, port: Int) -> Bool {
        let socketDescriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard socketDescriptor >= 0 else {
            return false
        }
        defer { close(socketDescriptor) }

        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(port).bigEndian

        let conversionResult = host.withCString { pointer in
            inet_pton(AF_INET, pointer, &address.sin_addr)
        }
        guard conversionResult == 1 else {
            return false
        }

        return withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
                Darwin.bind(socketDescriptor, socketAddress, socklen_t(MemoryLayout<sockaddr_in>.size)) == 0
            }
        }
    }
}

struct OpenPetsMCPPortAllocator {
    var portChecker: OpenPetsPortChecking
    var maximumPort: Int

    init(
        portChecker: OpenPetsPortChecking = OpenPetsSystemPortChecker(),
        maximumPort: Int = 65_535
    ) {
        self.portChecker = portChecker
        self.maximumPort = maximumPort
    }

    func availablePort(host: String, startingAt preferredPort: Int) throws -> Int {
        guard preferredPort > 0, preferredPort <= maximumPort else {
            throw OpenPetsFirstLaunchError.noAvailableMCPPort(preferredPort)
        }

        for port in preferredPort...maximumPort where portChecker.isPortAvailable(host: host, port: port) {
            return port
        }

        throw OpenPetsFirstLaunchError.noAvailableMCPPort(preferredPort)
    }
}

enum OpenPetsFirstLaunch {
    @discardableResult
    static func prepareConfigurationIfNeeded(
        configurationURL: URL = OpenPetsPaths.defaultConfigurationFileURL,
        portAllocator: OpenPetsMCPPortAllocator = OpenPetsMCPPortAllocator()
    ) throws -> Bool {
        guard !FileManager.default.fileExists(atPath: configurationURL.path) else {
            return false
        }

        var configuration = OpenPetsConfiguration()
        configuration.mcpPort = try portAllocator.availablePort(
            host: configuration.mcpHost,
            startingAt: configuration.mcpPort
        )
        try configuration.save(to: configurationURL)
        return true
    }
}

enum OpenPetsFirstLaunchError: Error, LocalizedError, Equatable {
    case noAvailableMCPPort(Int)

    var errorDescription: String? {
        switch self {
        case .noAvailableMCPPort(let preferredPort):
            "Could not find an available local MCP port starting at \(preferredPort)."
        }
    }
}
