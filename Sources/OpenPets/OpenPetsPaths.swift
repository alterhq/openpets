import Darwin
import Foundation

public enum OpenPetsPaths {
    public static var defaultSocketPath: String {
        "/tmp/openpets-\(getuid()).sock"
    }

    public static var defaultApplicationSupportDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return base.appendingPathComponent("OpenPets", isDirectory: true)
    }
}
