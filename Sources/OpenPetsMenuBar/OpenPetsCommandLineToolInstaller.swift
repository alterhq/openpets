import Foundation

struct OpenPetsCommandLineToolInstaller {
    var bundledExecutableURL: URL
    var installDirectoryURL: URL
    var fileManager: FileManager

    var installedExecutableURL: URL {
        installDirectoryURL.appendingPathComponent("openpets")
    }

    init(
        bundledExecutableURL: URL,
        installDirectoryURL: URL = OpenPetsCommandLineToolInstaller.defaultInstallDirectoryURL,
        fileManager: FileManager = .default
    ) {
        self.bundledExecutableURL = bundledExecutableURL
        self.installDirectoryURL = installDirectoryURL
        self.fileManager = fileManager
    }

    static var defaultInstallDirectoryURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local", isDirectory: true)
            .appendingPathComponent("bin", isDirectory: true)
    }

    static func bundledExecutableURL(bundle: Bundle = .main) throws -> URL {
        guard let executableDirectoryURL = bundle.executableURL?.deletingLastPathComponent() else {
            throw OpenPetsCommandLineToolInstallerError.missingBundleExecutable
        }

        let url = executableDirectoryURL.appendingPathComponent("openpets-cli")
        guard fileIsExecutable(at: url) else {
            throw OpenPetsCommandLineToolInstallerError.missingBundledCommandLineTool(url)
        }
        return url
    }

    func install() throws -> URL {
        guard Self.fileIsExecutable(at: bundledExecutableURL) else {
            throw OpenPetsCommandLineToolInstallerError.missingBundledCommandLineTool(bundledExecutableURL)
        }

        try fileManager.createDirectory(
            at: installDirectoryURL,
            withIntermediateDirectories: true
        )

        let destinationURL = installedExecutableURL
        if let existingTarget = try? fileManager.destinationOfSymbolicLink(atPath: destinationURL.path) {
            guard isReplaceableShimTarget(existingTarget, from: destinationURL) else {
                throw OpenPetsCommandLineToolInstallerError.destinationExists(destinationURL)
            }
            try fileManager.removeItem(at: destinationURL)
        } else if fileManager.fileExists(atPath: destinationURL.path) {
            throw OpenPetsCommandLineToolInstallerError.destinationExists(destinationURL)
        }

        do {
            try fileManager.createSymbolicLink(
                at: destinationURL,
                withDestinationURL: bundledExecutableURL
            )
        } catch let error as CocoaError where error.code == .fileWriteFileExists {
            throw OpenPetsCommandLineToolInstallerError.destinationExists(destinationURL)
        }
        return destinationURL
    }

    private static func fileIsExecutable(at url: URL) -> Bool {
        FileManager.default.isExecutableFile(atPath: url.path)
    }

    private func isReplaceableShimTarget(_ target: String, from shimURL: URL) -> Bool {
        let targetURL = URL(fileURLWithPath: target, relativeTo: shimURL.deletingLastPathComponent())
            .standardizedFileURL
        if targetURL == bundledExecutableURL.standardizedFileURL {
            return true
        }

        return targetURL.path.hasSuffix("/OpenPets.app/Contents/MacOS/openpets-cli")
    }
}

enum OpenPetsCommandLineToolInstallerError: Error, LocalizedError, Equatable {
    case missingBundleExecutable
    case missingBundledCommandLineTool(URL)
    case destinationExists(URL)

    var errorDescription: String? {
        switch self {
        case .missingBundleExecutable:
            "Could not locate the OpenPets app executable."
        case .missingBundledCommandLineTool(let url):
            "The bundled OpenPets command line tool is missing or is not executable at \(url.path)."
        case .destinationExists(let url):
            "A non-symlink file already exists at \(url.path). Move it aside, then install the OpenPets command line tool again."
        }
    }
}
