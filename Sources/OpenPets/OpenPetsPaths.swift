import Darwin
import Foundation

public enum OpenPetsPaths {
    public static var defaultSocketPath: String {
        "/tmp/openpets-\(getuid()).sock"
    }

    public static var defaultConfigurationDirectory: URL {
        if let xdgConfigHome = ProcessInfo.processInfo.environment["XDG_CONFIG_HOME"],
           !xdgConfigHome.isEmpty {
            return URL(fileURLWithPath: xdgConfigHome, isDirectory: true)
                .appendingPathComponent("openpets", isDirectory: true)
        }

        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
            .appendingPathComponent("openpets", isDirectory: true)
    }

    public static var defaultConfigurationFileURL: URL {
        defaultConfigurationDirectory.appendingPathComponent("config.json")
    }

    public static var defaultPositionStoreURL: URL {
        defaultConfigurationDirectory.appendingPathComponent("positions.json")
    }

    public static var defaultApplicationSupportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return base.appendingPathComponent("OpenPets", isDirectory: true)
    }

    public static var defaultInstalledPetsDirectory: URL {
        defaultApplicationSupportDirectory.appendingPathComponent("Pets", isDirectory: true)
    }

    public static var defaultCodexPetsDirectory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex", isDirectory: true)
            .appendingPathComponent("pets", isDirectory: true)
    }

    public static var defaultUserDataPetsDirectory: URL {
        if let xdgDataHome = ProcessInfo.processInfo.environment["XDG_DATA_HOME"],
           !xdgDataHome.isEmpty {
            return URL(fileURLWithPath: xdgDataHome, isDirectory: true)
                .appendingPathComponent("openpets", isDirectory: true)
                .appendingPathComponent("pets", isDirectory: true)
        }

        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local", isDirectory: true)
            .appendingPathComponent("share", isDirectory: true)
            .appendingPathComponent("openpets", isDirectory: true)
            .appendingPathComponent("pets", isDirectory: true)
    }

    public static var defaultDiscoveredPetsDirectories: [URL] {
        [
            defaultCodexPetsDirectory,
            defaultUserDataPetsDirectory,
            defaultConfigurationDirectory.appendingPathComponent("pets", isDirectory: true),
            defaultConfigurationDirectory.appendingPathComponent("Pets", isDirectory: true),
            defaultConfigurationDirectory
        ]
    }
}
