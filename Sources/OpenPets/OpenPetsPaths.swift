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
}
