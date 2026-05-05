import Foundation

public enum OpenPetsInstallError: Error, LocalizedError, Equatable {
    case invalidSource(String)
    case invalidInstallURL(URL)
    case missingDownloadURL(URL)
    case downloadFailed(String)
    case unsafeArchiveEntry(String)
    case archiveExtractionFailed(String)
    case noPetBundleFound(URL)
    case invalidPetID(String)

    public var errorDescription: String? {
        switch self {
        case .invalidSource(let source):
            "Invalid install source: \(source)"
        case .invalidInstallURL(let url):
            "Invalid OpenPets install URL: \(url.absoluteString)"
        case .missingDownloadURL(let url):
            "Install URL does not include a download URL: \(url.absoluteString)"
        case .downloadFailed(let message):
            "Could not download pet bundle: \(message)"
        case .unsafeArchiveEntry(let entry):
            "Pet bundle archive contains an unsafe path: \(entry)"
        case .archiveExtractionFailed(let message):
            "Could not extract pet bundle: \(message)"
        case .noPetBundleFound(let url):
            "No pet.json bundle was found in \(url.path)"
        case .invalidPetID(let id):
            "Invalid pet id: \(id)"
        }
    }
}

public struct OpenPetsInstallResult: Equatable, Sendable {
    public var petID: String
    public var displayName: String
    public var directoryURL: URL
    public var activated: Bool

    public init(petID: String, displayName: String, directoryURL: URL, activated: Bool) {
        self.petID = petID
        self.displayName = displayName
        self.directoryURL = directoryURL
        self.activated = activated
    }
}

public struct OpenPetsPreparedInstall: Equatable, Sendable {
    public var petID: String
    public var displayName: String
    public var description: String
    public var bundleURL: URL
    public var stagingDirectoryURL: URL

    public init(
        petID: String,
        displayName: String,
        description: String,
        bundleURL: URL,
        stagingDirectoryURL: URL
    ) {
        self.petID = petID
        self.displayName = displayName
        self.description = description
        self.bundleURL = bundleURL
        self.stagingDirectoryURL = stagingDirectoryURL
    }

    public func cleanup() {
        try? FileManager.default.removeItem(at: stagingDirectoryURL)
    }
}

public struct OpenPetsInstallRequest: Equatable, Sendable {
    public var downloadURL: URL
    public var requestedPetID: String?

    public init(downloadURL: URL, requestedPetID: String? = nil) {
        self.downloadURL = downloadURL
        self.requestedPetID = requestedPetID
    }

    public static func parse(_ source: String, registryBaseURL: URL = URL(string: "https://openpets.sh")!) throws -> OpenPetsInstallRequest {
        if let url = URL(string: source), url.scheme == "openpets" {
            return try parseDeepLink(url)
        }

        if let url = URL(string: source), ["http", "https", "file"].contains(url.scheme?.lowercased()) {
            return OpenPetsInstallRequest(downloadURL: url)
        }

        if source.hasPrefix("/") || source.hasPrefix("~") {
            return OpenPetsInstallRequest(downloadURL: URL(fileURLWithPath: NSString(string: source).expandingTildeInPath))
        }

        guard isValidPetID(source) else {
            throw OpenPetsInstallError.invalidSource(source)
        }

        let ticketURL = registryBaseURL
            .appendingPathComponent("api")
            .appendingPathComponent("pets")
            .appendingPathComponent(source)
            .appendingPathComponent("install-ticket")
        return try fetchInstallTicket(from: ticketURL, requestedPetID: source)
    }

    public static func parseDeepLink(_ url: URL) throws -> OpenPetsInstallRequest {
        guard url.scheme == "openpets", url.host == "install" || url.path == "/install" else {
            throw OpenPetsInstallError.invalidInstallURL(url)
        }
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let items = components?.queryItems ?? []
        guard
            let rawDownloadURL = items.first(where: { $0.name == "url" })?.value,
            let downloadURL = URL(string: rawDownloadURL)
        else {
            throw OpenPetsInstallError.missingDownloadURL(url)
        }
        let petID = items.first(where: { $0.name == "id" })?.value
        return OpenPetsInstallRequest(downloadURL: downloadURL, requestedPetID: petID)
    }

    private static func fetchInstallTicket(from url: URL, requestedPetID: String) throws -> OpenPetsInstallRequest {
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try URLSession.shared.synchronousData(for: request)
        guard let httpResponse = response as? HTTPURLResponse, 200..<300 ~= httpResponse.statusCode else {
            throw OpenPetsInstallError.downloadFailed("registry ticket request failed")
        }
        let ticket = try JSONDecoder().decode(InstallTicketResponse.self, from: data)
        return OpenPetsInstallRequest(downloadURL: ticket.downloadUrl, requestedPetID: requestedPetID)
    }

    private struct InstallTicketResponse: Decodable {
        var downloadUrl: URL
    }
}

public struct OpenPetsPetInstaller: Sendable {
    public var installedPetsDirectory: URL
    public var configurationURL: URL

    public init(
        installedPetsDirectory: URL = OpenPetsPaths.defaultInstalledPetsDirectory,
        configurationURL: URL = OpenPetsPaths.defaultConfigurationFileURL
    ) {
        self.installedPetsDirectory = installedPetsDirectory
        self.configurationURL = configurationURL
    }

    @discardableResult
    public func install(source: String, activate: Bool = true) throws -> OpenPetsInstallResult {
        let preparedInstall = try prepare(source: source)
        defer { preparedInstall.cleanup() }
        return try install(prepared: preparedInstall, activate: activate)
    }

    @discardableResult
    public func install(request: OpenPetsInstallRequest, activate: Bool = true) throws -> OpenPetsInstallResult {
        let preparedInstall = try prepare(request: request)
        defer { preparedInstall.cleanup() }
        return try install(prepared: preparedInstall, activate: activate)
    }

    public func prepare(source: String) throws -> OpenPetsPreparedInstall {
        try prepare(request: OpenPetsInstallRequest.parse(source))
    }

    public func prepare(request: OpenPetsInstallRequest) throws -> OpenPetsPreparedInstall {
        let workDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-install-\(UUID().uuidString)", isDirectory: true)
        let archiveURL = workDirectory.appendingPathComponent("pet.zip")
        let extractURL = workDirectory.appendingPathComponent("extracted", isDirectory: true)

        do {
            try FileManager.default.createDirectory(at: extractURL, withIntermediateDirectories: true)
            try download(from: request.downloadURL, to: archiveURL)
            try validateZipEntries(archiveURL)
            try extractZip(archiveURL, to: extractURL)

            let bundleURL = try locatePetBundle(in: extractURL)
            let bundle = try PetBundle.load(from: bundleURL)
            try validate(bundle: bundle, requestedPetID: request.requestedPetID)

            return OpenPetsPreparedInstall(
                petID: bundle.manifest.id,
                displayName: bundle.manifest.displayName,
                description: bundle.manifest.description,
                bundleURL: bundleURL,
                stagingDirectoryURL: workDirectory
            )
        } catch {
            try? FileManager.default.removeItem(at: workDirectory)
            throw error
        }
    }

    @discardableResult
    public func install(prepared preparedInstall: OpenPetsPreparedInstall, activate: Bool = true) throws -> OpenPetsInstallResult {
        let bundle = try PetBundle.load(from: preparedInstall.bundleURL)
        try validate(bundle: bundle, requestedPetID: preparedInstall.petID)

        try FileManager.default.createDirectory(at: installedPetsDirectory, withIntermediateDirectories: true)
        let destinationURL = installedPetsDirectory.appendingPathComponent(bundle.manifest.id, isDirectory: true)
        let replacementURL = installedPetsDirectory.appendingPathComponent(".\(bundle.manifest.id)-\(UUID().uuidString)", isDirectory: true)
        if FileManager.default.fileExists(atPath: replacementURL.path) {
            try FileManager.default.removeItem(at: replacementURL)
        }
        try FileManager.default.copyItem(at: preparedInstall.bundleURL, to: replacementURL)
        if FileManager.default.fileExists(atPath: destinationURL.path) {
            try FileManager.default.removeItem(at: destinationURL)
        }
        try FileManager.default.moveItem(at: replacementURL, to: destinationURL)

        if activate {
            var configuration = try OpenPetsConfiguration.loadOrCreateDefault(at: configurationURL)
            configuration.activePetID = bundle.manifest.id
            try configuration.save(to: configurationURL)
        }

        return OpenPetsInstallResult(
            petID: bundle.manifest.id,
            displayName: bundle.manifest.displayName,
            directoryURL: destinationURL,
            activated: activate
        )
    }

    public static func isValidPetID(_ id: String) -> Bool {
        guard !id.isEmpty, id.count <= 80 else {
            return false
        }
        return id.range(of: #"^[A-Za-z0-9][A-Za-z0-9._-]*$"#, options: .regularExpression) != nil
    }

    private func download(from url: URL, to destinationURL: URL) throws {
        if url.isFileURL {
            try FileManager.default.copyItem(at: url, to: destinationURL)
            return
        }

        let (data, response) = try URLSession.shared.synchronousData(for: URLRequest(url: url))
        guard let httpResponse = response as? HTTPURLResponse, 200..<300 ~= httpResponse.statusCode else {
            throw OpenPetsInstallError.downloadFailed("HTTP request failed")
        }
        try data.write(to: destinationURL, options: .atomic)
    }

    private func validateZipEntries(_ archiveURL: URL) throws {
        let output = try runProcess("/usr/bin/unzip", arguments: ["-Z", "-1", archiveURL.path])
        for entry in output.split(separator: "\n").map(String.init) {
            if entry.hasPrefix("/") || entry.contains("../") || entry == ".." || entry.contains("/..") {
                throw OpenPetsInstallError.unsafeArchiveEntry(entry)
            }
        }
    }

    private func extractZip(_ archiveURL: URL, to destinationURL: URL) throws {
        do {
            _ = try runProcess("/usr/bin/ditto", arguments: ["-x", "-k", archiveURL.path, destinationURL.path])
        } catch {
            throw OpenPetsInstallError.archiveExtractionFailed(error.localizedDescription)
        }
    }

    private func locatePetBundle(in directoryURL: URL) throws -> URL {
        if FileManager.default.fileExists(atPath: directoryURL.appendingPathComponent("pet.json").path) {
            return directoryURL
        }

        let children = try FileManager.default.contentsOfDirectory(
            at: directoryURL,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        )

        for child in children {
            var isDirectory: ObjCBool = false
            guard FileManager.default.fileExists(atPath: child.path, isDirectory: &isDirectory), isDirectory.boolValue else {
                continue
            }
            if FileManager.default.fileExists(atPath: child.appendingPathComponent("pet.json").path) {
                return child
            }
        }

        throw OpenPetsInstallError.noPetBundleFound(directoryURL)
    }

    private func validate(bundle: PetBundle, requestedPetID: String?) throws {
        guard Self.isValidPetID(bundle.manifest.id) else {
            throw OpenPetsInstallError.invalidPetID(bundle.manifest.id)
        }
        if let requestedPetID, !requestedPetID.isEmpty, requestedPetID != bundle.manifest.id {
            throw OpenPetsInstallError.invalidPetID("\(bundle.manifest.id) does not match requested id \(requestedPetID)")
        }
    }

    private func runProcess(_ executable: String, arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        let outputData = output.fileHandleForReading.readDataToEndOfFile()
        let errorData = error.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            let message = String(data: errorData, encoding: .utf8) ?? "process failed"
            throw OpenPetsInstallError.archiveExtractionFailed(message)
        }
        return String(data: outputData, encoding: .utf8) ?? ""
    }
}

private extension OpenPetsInstallRequest {
    static func isValidPetID(_ id: String) -> Bool {
        OpenPetsPetInstaller.isValidPetID(id)
    }
}

private extension URLSession {
    func synchronousData(for request: URLRequest) throws -> (Data, URLResponse) {
        let semaphore = DispatchSemaphore(value: 0)
        final class Box: @unchecked Sendable {
            var result: Result<(Data, URLResponse), Error>?
        }
        let box = Box()
        let task = dataTask(with: request) { data, response, error in
            if let error {
                box.result = .failure(error)
            } else if let data, let response {
                box.result = .success((data, response))
            } else {
                box.result = .failure(OpenPetsInstallError.downloadFailed("empty response"))
            }
            semaphore.signal()
        }
        task.resume()
        semaphore.wait()
        return try box.result!.get()
    }
}
