import Foundation
import OpenPetsCore

enum OpenPetsAgentKind: CaseIterable, Equatable, Hashable, Sendable {
    case codex
    case claude
    case pi
    case openCode
    case zed

    var displayName: String {
        switch self {
        case .codex:
            "Codex"
        case .claude:
            "Claude Code"
        case .pi:
            "Pi"
        case .openCode:
            "OpenCode"
        case .zed:
            "Zed"
        }
    }

    var executableName: String {
        switch self {
        case .codex:
            "codex"
        case .claude:
            "claude"
        case .pi:
            "pi"
        case .openCode:
            "opencode"
        case .zed:
            "zed"
        }
    }

    var installGuideURL: URL {
        switch self {
        case .codex:
            URL(string: "https://developers.openai.com/codex/mcp")!
        case .claude:
            URL(string: "https://code.claude.com/docs/en/mcp")!
        case .pi:
            URL(string: "https://pi.dev/packages/pi-mcp-extension")!
        case .openCode:
            URL(string: "https://opencode.ai/docs/")!
        case .zed:
            URL(string: "https://zed.dev/docs/ai/mcp")!
        }
    }
}

enum OpenPetsAgentSetupState: Equatable, Sendable {
    case missing
    case installed
    case configured
    case configuredDifferentURL
    case failed(String)
}

struct OpenPetsAgentDetection: Equatable, Sendable {
    var kind: OpenPetsAgentKind
    var state: OpenPetsAgentSetupState
    var executableURL: URL?
    var detail: String
    var setupPathsAvailable: Bool
}

struct OpenPetsProcessResult: Equatable, Sendable {
    var terminationStatus: Int32
    var standardOutput: String
    var standardError: String

    var succeeded: Bool {
        terminationStatus == 0
    }
}

protocol OpenPetsProcessRunning: Sendable {
    func run(executableURL: URL, arguments: [String]) throws -> OpenPetsProcessResult
}

struct OpenPetsDefaultProcessRunner: OpenPetsProcessRunning, Sendable {
    func run(executableURL: URL, arguments: [String]) throws -> OpenPetsProcessResult {
        let process = Process()
        process.executableURL = executableURL
        process.arguments = arguments
        process.environment = Self.environment(for: executableURL)

        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        try process.run()
        process.waitUntilExit()

        let output = outputPipe.fileHandleForReading.readDataToEndOfFile()
        let error = errorPipe.fileHandleForReading.readDataToEndOfFile()
        return OpenPetsProcessResult(
            terminationStatus: process.terminationStatus,
            standardOutput: String(data: output, encoding: .utf8) ?? "",
            standardError: String(data: error, encoding: .utf8) ?? ""
        )
    }

    static func environment(
        for executableURL: URL,
        baseEnvironment: [String: String] = ProcessInfo.processInfo.environment
    ) -> [String: String] {
        var environment = baseEnvironment
        let currentPath = baseEnvironment["PATH"] ?? ""
        let executableDirectory = executableURL.deletingLastPathComponent().path
        let pathDirectories = [
            executableDirectory,
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin"
        ] + currentPath
            .split(separator: ":")
            .map(String.init)

        var seen = Set<String>()
        environment["PATH"] = pathDirectories
            .filter { !$0.isEmpty }
            .filter { seen.insert($0).inserted }
            .joined(separator: ":")
        return environment
    }
}

struct OpenPetsAgentDetector: Sendable {
    var processRunner: OpenPetsProcessRunning
    var shellURL: URL
    var searchDirectories: [URL]
    var codexConfigurationURL: URL
    var claudeConfigurationURL: URL
    var piMCPConfigurationURL: URL
    var openCodeConfigurationURL: URL
    var zedConfigurationURL: URL

    init(
        processRunner: OpenPetsProcessRunning = OpenPetsDefaultProcessRunner(),
        shellURL: URL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"),
        searchDirectories: [URL] = OpenPetsAgentDetector.defaultSearchDirectories(),
        codexConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex", isDirectory: true)
            .appendingPathComponent("config.toml"),
        claudeConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude.json"),
        piMCPConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".pi", isDirectory: true)
            .appendingPathComponent("agent", isDirectory: true)
            .appendingPathComponent("mcp.json"),
        openCodeConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
            .appendingPathComponent("opencode", isDirectory: true)
            .appendingPathComponent("opencode.json"),
        zedConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
            .appendingPathComponent("zed", isDirectory: true)
            .appendingPathComponent("settings.json")
    ) {
        self.processRunner = processRunner
        self.shellURL = shellURL
        self.searchDirectories = searchDirectories
        self.codexConfigurationURL = codexConfigurationURL
        self.claudeConfigurationURL = claudeConfigurationURL
        self.piMCPConfigurationURL = piMCPConfigurationURL
        self.openCodeConfigurationURL = openCodeConfigurationURL
        self.zedConfigurationURL = zedConfigurationURL
    }

    func detectAll(mcpURL: String) -> [OpenPetsAgentDetection] {
        OpenPetsAgentKind.allCases.map { detect($0, mcpURL: mcpURL) }
    }

    func detect(_ kind: OpenPetsAgentKind, mcpURL: String) -> OpenPetsAgentDetection {
        let setupPathStatus = setupPathStatus(for: kind)
        do {
            guard let executableURL = try locateExecutable(for: kind) else {
                return OpenPetsAgentDetection(
                    kind: kind,
                    state: .missing,
                    executableURL: nil,
                    detail: "\(kind.executableName) was not found. Checked shell PATH and \(searchDirectories.count) common install locations. \(setupPathStatus.detail)",
                    setupPathsAvailable: setupPathStatus.available
                )
            }

            let configuredState = configuredState(for: kind, executableURL: executableURL, mcpURL: mcpURL)
            return OpenPetsAgentDetection(
                kind: kind,
                state: configuredState.state,
                executableURL: executableURL,
                detail: "\(configuredState.detail) \(setupPathStatus.detail)",
                setupPathsAvailable: setupPathStatus.available
            )
        } catch {
            return OpenPetsAgentDetection(
                kind: kind,
                state: .failed(error.localizedDescription),
                executableURL: nil,
                detail: "\(error.localizedDescription) \(setupPathStatus.detail)",
                setupPathsAvailable: setupPathStatus.available
            )
        }
    }

    private func locateExecutable(for kind: OpenPetsAgentKind) throws -> URL? {
        for directoryURL in searchDirectories {
            let executableURL = directoryURL.appendingPathComponent(kind.executableName)
            if FileManager.default.isExecutableFile(atPath: executableURL.path) {
                return executableURL
            }
        }

        let result = try processRunner.run(
            executableURL: shellURL,
            arguments: ["-lc", "command -v \(kind.executableName)"]
        )
        let path = result.standardOutput.trimmingCharacters(in: .whitespacesAndNewlines)
        if result.succeeded, !path.isEmpty {
            let url = URL(fileURLWithPath: path)
            if FileManager.default.isExecutableFile(atPath: url.path) {
                return url
            }
        }

        return nil
    }

    private func configuredState(
        for kind: OpenPetsAgentKind,
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        switch kind {
        case .codex:
            return codexConfiguredState(executableURL: executableURL, mcpURL: mcpURL)
        case .claude:
            return claudeConfiguredState(executableURL: executableURL, mcpURL: mcpURL)
        case .pi:
            return piConfiguredState(executableURL: executableURL, mcpURL: mcpURL)
        case .openCode:
            return openCodeConfiguredState(executableURL: executableURL, mcpURL: mcpURL)
        case .zed:
            return zedConfiguredState(executableURL: executableURL, mcpURL: mcpURL)
        }
    }

    private func codexConfiguredState(
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        guard
            let config = try? String(contentsOf: codexConfigurationURL),
            config.contains("openpets")
        else {
            return (.installed, "Installed at \(executableURL.path). OpenPets MCP is not configured yet.")
        }

        if config.contains(mcpURL) {
            return (.configured, "OpenPets MCP is configured at \(mcpURL).")
        }
        return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
    }

    private func openCodeConfiguredState(
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        let server = openCodeConfigurationURLs().lazy.compactMap { configurationURL in
            userMCPServer(
                in: configurationURL,
                sectionKey: "mcp",
                name: "openpets",
                allowJSONC: true
            )
        }.first

        guard
            let server,
            let configuredURL = server["url"] as? String
        else {
            return (.installed, "Installed at \(executableURL.path). OpenPets MCP is not configured yet.")
        }

        guard
            server["type"] as? String == "remote",
            server["enabled"] as? Bool != false
        else {
            return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
        }

        if configuredURL == mcpURL {
            return (.configured, "OpenPets MCP is configured at \(mcpURL).")
        }
        return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
    }

    private func piConfiguredState(
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        guard let configuredURL = userMCPServer(
            in: piMCPConfigurationURL,
            sectionKey: "mcpServers",
            name: "openpets"
        )?["url"] as? String else {
            return (.installed, "Installed at \(executableURL.path). OpenPets MCP is not configured yet.")
        }

        if configuredURL == mcpURL {
            return (.configured, "OpenPets MCP is configured at \(mcpURL).")
        }
        return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
    }

    private func zedConfiguredState(
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        guard let server = userMCPServer(
            in: zedConfigurationURL,
            sectionKey: "context_servers",
            name: "openpets",
            allowJSONC: true
        ), let configuredURL = server["url"] as? String else {
            return (.installed, "Installed at \(executableURL.path). OpenPets MCP is not configured yet.")
        }

        let headers = server["headers"] as? [String: Any]
        let authorizationHeader = headers?["Authorization"] as? String
        guard authorizationHeader?.isEmpty == false else {
            return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to avoid Zed OAuth prompts.")
        }

        if configuredURL == mcpURL {
            return (.configured, "OpenPets MCP is configured at \(mcpURL).")
        }
        return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
    }

    private func claudeConfiguredState(
        executableURL: URL,
        mcpURL: String
    ) -> (state: OpenPetsAgentSetupState, detail: String) {
        guard let configuredURL = userMCPServer(
            in: claudeConfigurationURL,
            sectionKey: "mcpServers",
            name: "openpets"
        )?["url"] as? String else {
            return (.installed, "Installed at \(executableURL.path). OpenPets MCP is not configured yet.")
        }

        if configuredURL == mcpURL {
            return (.configured, "OpenPets MCP is configured at \(mcpURL).")
        }
        return (.configuredDifferentURL, "OpenPets MCP exists, but should be updated to the current server URL.")
    }

    private func userMCPServer(
        in configurationURL: URL,
        sectionKey: String,
        name: String,
        allowJSONC: Bool = false
    ) -> [String: Any]? {
        guard
            let json = try? OpenPetsMCPJSONConfiguration.readJSONObject(from: configurationURL, allowJSONC: allowJSONC),
            let servers = json[sectionKey] as? [String: Any],
            let server = servers[name] as? [String: Any]
        else {
            return nil
        }

        return server
    }

    private func openCodeConfigurationURLs() -> [URL] {
        let baseURL = openCodeConfigurationURL.deletingPathExtension()
        let jsoncURL = baseURL.appendingPathExtension("jsonc")
        let jsonURL = baseURL.appendingPathExtension("json")
        return [jsoncURL, jsonURL]
    }
}

extension OpenPetsAgentDetector {
    static func defaultSearchDirectories(homeDirectoryURL: URL = FileManager.default.homeDirectoryForCurrentUser) -> [URL] {
        var directories = pathEnvironmentDirectories()
        directories.append(contentsOf: [
            homeDirectoryURL.appendingPathComponent(".opencode/bin", isDirectory: true),
            homeDirectoryURL.appendingPathComponent(".local/bin", isDirectory: true),
            homeDirectoryURL.appendingPathComponent("bin", isDirectory: true),
            URL(fileURLWithPath: "/opt/homebrew/bin", isDirectory: true),
            URL(fileURLWithPath: "/usr/local/bin", isDirectory: true),
            URL(fileURLWithPath: "/usr/bin", isDirectory: true)
        ])

        let nvmVersionsURL = homeDirectoryURL.appendingPathComponent(".nvm/versions/node", isDirectory: true)
        if let versions = try? FileManager.default.contentsOfDirectory(
            at: nvmVersionsURL,
            includingPropertiesForKeys: nil
        ) {
            directories.append(contentsOf: versions.prefix(20).map { $0.appendingPathComponent("bin", isDirectory: true) })
        }

        let fnmMultishellsURL = homeDirectoryURL.appendingPathComponent("Library/Caches/fnm_multishells", isDirectory: true)
        if let shells = try? FileManager.default.contentsOfDirectory(
            at: fnmMultishellsURL,
            includingPropertiesForKeys: nil
        ) {
            directories.append(contentsOf: shells.prefix(10).map { $0.appendingPathComponent("bin", isDirectory: true) })
        }

        var seen = Set<String>()
        return directories.filter { seen.insert($0.standardizedFileURL.path).inserted }
    }

    private static func pathEnvironmentDirectories() -> [URL] {
        let path = ProcessInfo.processInfo.environment["PATH"] ?? ""
        return path
            .split(separator: ":")
            .map(String.init)
            .filter { !$0.isEmpty }
            .map { URL(fileURLWithPath: $0, isDirectory: true) }
    }
}

private extension OpenPetsAgentDetector {
    func setupPathStatus(for kind: OpenPetsAgentKind) -> (available: Bool, detail: String) {
        let urls = setupURLs(for: kind)
        let blockedURLs = urls.filter { !canWriteToExistingOrNearestParent($0) }
        guard blockedURLs.isEmpty else {
            return (
                false,
                "Setup path blocked: \(blockedURLs.map(\.path).joined(separator: ", "))."
            )
        }

        return (
            true,
            "Setup paths writable: \(urls.map(\.path).joined(separator: ", "))."
        )
    }

    func setupURLs(for kind: OpenPetsAgentKind) -> [URL] {
        let homeDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        switch kind {
        case .codex:
            return [
                homeDirectoryURL.appendingPathComponent(".codex", isDirectory: true)
            ]
        case .claude:
            return [
                homeDirectoryURL.appendingPathComponent(".claude", isDirectory: true)
            ]
        case .pi:
            return [
                piMCPConfigurationURL.deletingLastPathComponent()
            ]
        case .openCode:
            return [
                openCodeConfigurationURL.deletingLastPathComponent()
            ]
        case .zed:
            return [
                zedConfigurationURL.deletingLastPathComponent()
            ]
        }
    }

    func canWriteToExistingOrNearestParent(_ url: URL) -> Bool {
        var candidateURL = url
        while !FileManager.default.fileExists(atPath: candidateURL.path) {
            let parentURL = candidateURL.deletingLastPathComponent()
            guard parentURL.path != candidateURL.path else {
                return false
            }
            candidateURL = parentURL
        }
        return FileManager.default.isWritableFile(atPath: candidateURL.path)
    }
}

struct OpenPetsAgentInstallCommand: Equatable, Sendable {
    var executableURL: URL
    var arguments: [String]
    var previewTextOverride: String? = nil

    var previewText: String {
        if let previewTextOverride {
            return previewTextOverride
        }
        return ([executableURL.path] + arguments).map(openPetsShellQuoted).joined(separator: " ")
    }
}

enum OpenPetsAgentSetupOperation: Equatable, Sendable {
    case install
    case uninstall
}

struct OpenPetsAgentInstallResult: Equatable, Sendable {
    var kind: OpenPetsAgentKind
    var operation: OpenPetsAgentSetupOperation
    var command: OpenPetsAgentInstallCommand
    var processResult: OpenPetsProcessResult

    var succeeded: Bool {
        processResult.succeeded
    }

    var message: String {
        if succeeded {
            switch operation {
            case .install:
                return "\(kind.displayName) MCP setup completed."
            case .uninstall:
                return "\(kind.displayName) MCP setup removed."
            }
        }

        let detail = processResult.standardError.trimmingCharacters(in: .whitespacesAndNewlines)
        if !detail.isEmpty {
            return detail
        }
        switch operation {
        case .install:
            return "\(kind.displayName) MCP setup failed."
        case .uninstall:
            return "\(kind.displayName) MCP removal failed."
        }
    }
}

struct OpenPetsAgentSetupInstaller: Sendable {
    var processRunner: OpenPetsProcessRunning
    var piMCPConfigurationURL: URL
    var openCodeConfigurationURL: URL
    var zedConfigurationURL: URL

    init(
        processRunner: OpenPetsProcessRunning = OpenPetsDefaultProcessRunner(),
        piMCPConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".pi", isDirectory: true)
            .appendingPathComponent("agent", isDirectory: true)
            .appendingPathComponent("mcp.json"),
        openCodeConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
            .appendingPathComponent("opencode", isDirectory: true)
            .appendingPathComponent("opencode.json"),
        zedConfigurationURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".config", isDirectory: true)
            .appendingPathComponent("zed", isDirectory: true)
            .appendingPathComponent("settings.json")
    ) {
        self.processRunner = processRunner
        self.piMCPConfigurationURL = piMCPConfigurationURL
        self.openCodeConfigurationURL = openCodeConfigurationURL
        self.zedConfigurationURL = zedConfigurationURL
    }

    func command(kind: OpenPetsAgentKind, executableURL: URL, mcpURL: String) -> OpenPetsAgentInstallCommand {
        switch kind {
        case .codex:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: ["mcp", "add", "openpets", "--url", mcpURL]
            )
        case .claude:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: ["mcp", "add", "--transport", "http", "--scope", "user", "openpets", mcpURL]
            )
        case .pi:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: ["install", "npm:pi-mcp-extension"],
                previewTextOverride: """
                \(openPetsShellQuoted(executableURL.path)) install npm:pi-mcp-extension
                Write \(piMCPConfigurationURL.path) with mcpServers.openpets.url = \(mcpURL)
                """
            )
        case .openCode:
            let configurationURL = openCodeWritableConfigurationURL()
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: [],
                previewTextOverride: "Write \(configurationURL.path) with mcp.openpets.url = \(mcpURL)"
            )
        case .zed:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: [],
                previewTextOverride: "Write \(zedConfigurationURL.path) with context_servers.openpets.url = \(mcpURL) and a local Authorization header"
            )
        }
    }

    func uninstallCommand(kind: OpenPetsAgentKind, executableURL: URL) -> OpenPetsAgentInstallCommand {
        switch kind {
        case .codex:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: ["mcp", "remove", "openpets"]
            )
        case .claude:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: ["mcp", "remove", "--scope", "user", "openpets"]
            )
        case .pi:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: [],
                previewTextOverride: "Remove mcpServers.openpets from \(piMCPConfigurationURL.path)"
            )
        case .openCode:
            let configurationURL = openCodeWritableConfigurationURL()
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: [],
                previewTextOverride: "Remove mcp.openpets from \(configurationURL.path)"
            )
        case .zed:
            return OpenPetsAgentInstallCommand(
                executableURL: executableURL,
                arguments: [],
                previewTextOverride: "Remove context_servers.openpets from \(zedConfigurationURL.path)"
            )
        }
    }

    func install(kind: OpenPetsAgentKind, executableURL: URL, mcpURL: String) throws -> OpenPetsAgentInstallResult {
        let installCommand = command(kind: kind, executableURL: executableURL, mcpURL: mcpURL)
        if kind == .zed {
            try OpenPetsMCPJSONConfiguration.upsertZedRemoteServer(
                name: "openpets",
                url: mcpURL,
                in: zedConfigurationURL
            )
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .install,
                command: installCommand,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 0,
                    standardOutput: "",
                    standardError: ""
                )
            )
        }

        if kind == .openCode {
            let configurationURL = openCodeWritableConfigurationURL()
            try OpenPetsMCPJSONConfiguration.upsertOpenCodeRemoteServer(
                name: "openpets",
                url: mcpURL,
                in: configurationURL
            )
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .install,
                command: installCommand,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 0,
                    standardOutput: "",
                    standardError: ""
                )
            )
        }

        if kind == .pi {
            let result = try processRunner.run(
                executableURL: installCommand.executableURL,
                arguments: installCommand.arguments
            )
            if result.succeeded {
                try OpenPetsMCPJSONConfiguration.upsertHTTPServer(
                    name: "openpets",
                    url: mcpURL,
                    in: piMCPConfigurationURL
                )
            }
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .install,
                command: installCommand,
                processResult: result
            )
        }

        let result = try processRunner.run(
            executableURL: installCommand.executableURL,
            arguments: installCommand.arguments
        )
        return OpenPetsAgentInstallResult(
            kind: kind,
            operation: .install,
            command: installCommand,
            processResult: result
        )
    }

    func uninstall(kind: OpenPetsAgentKind, executableURL: URL) throws -> OpenPetsAgentInstallResult {
        let command = uninstallCommand(kind: kind, executableURL: executableURL)
        if kind == .zed {
            try OpenPetsMCPJSONConfiguration.removeServer(
                name: "openpets",
                sectionKey: "context_servers",
                from: zedConfigurationURL
            )
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .uninstall,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 0,
                    standardOutput: "",
                    standardError: ""
                )
            )
        }

        if kind == .openCode {
            let configurationURL = openCodeWritableConfigurationURL()
            try OpenPetsMCPJSONConfiguration.removeServer(
                name: "openpets",
                sectionKey: "mcp",
                from: configurationURL
            )
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .uninstall,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 0,
                    standardOutput: "",
                    standardError: ""
                )
            )
        }

        if kind == .pi {
            try OpenPetsMCPJSONConfiguration.removeServer(
                name: "openpets",
                sectionKey: "mcpServers",
                from: piMCPConfigurationURL
            )
            return OpenPetsAgentInstallResult(
                kind: kind,
                operation: .uninstall,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 0,
                    standardOutput: "",
                    standardError: ""
                )
            )
        }

        let result = try processRunner.run(
            executableURL: command.executableURL,
            arguments: command.arguments
        )
        return OpenPetsAgentInstallResult(
            kind: kind,
            operation: .uninstall,
            command: command,
            processResult: result
        )
    }

    private func openCodeWritableConfigurationURL() -> URL {
        for configurationURL in openCodeConfigurationURLs() where FileManager.default.fileExists(atPath: configurationURL.path) {
            return configurationURL
        }
        return openCodeConfigurationURL
    }

    private func openCodeConfigurationURLs() -> [URL] {
        let baseURL = openCodeConfigurationURL.deletingPathExtension()
        let jsoncURL = baseURL.appendingPathExtension("jsonc")
        let jsonURL = baseURL.appendingPathExtension("json")
        return [jsoncURL, jsonURL]
    }
}

enum OpenPetsAssistantInstructions {
    static let snippet = """
    ## OpenPets MCP

    When OpenPets MCP tools are available, use the desktop pet as the visible task-state channel.

    Before any final response that reports a task result, answer, decision, blocker, or completed action, call `notify` with the same outcome you are about to report to the user. This applies even to small requests, direct answers, file searches, shell commands, edits, reviews, and failed attempts.

    For non-trivial or multi-step work, also call `notify` with `running` when work starts or when meaningful progress changes. Do not notify for every internal step.

    Threaded message workflow:

    1. When starting a distinct task or agent run, call `notify` without `threadId`. OpenPets creates a new bubble and returns a `threadId`.
    2. Store that `threadId` for the life of the task.
    3. For every later progress, waiting, review, failed, done, or follow-up update for that same task, call `notify` with the same `threadId` so the existing bubble is replaced.
    4. If multiple tasks or agents run concurrently, each task or agent should keep its own `threadId`; do not reuse one task's `threadId` for another task.
    5. Use `clear_pet_message` with a task's `threadId` only when that specific task bubble is no longer relevant.

    If the first `notify` call fails or indicates the pet is not running/visible, call `wake_pet` and retry `notify` once before sending the final response. Do not call `get_openpets_status` before normal updates.

    Use statuses consistently:

    - `running`: work is actively in progress
    - `done`: the requested task completed successfully
    - `failed`: the task failed or hit a blocker
    - `review`: user review, confirmation, or attention is needed
    - `waiting`: work is paused or waiting on external input
    - `message`: neutral informational message that is not a task outcome

    Final notifications must be specific. Include the actual outcome in `text`, such as "No README file was found in `/Users/sam/code/openpets`," rather than generic text like "Done."

    Keep `title` short and put useful detail in `text`. Use `ttlSeconds` only for temporary updates; omit it when the message should remain visible until replaced by another notify call with the same `threadId` or cleared.

    Use `play_pet_animation` only for non-message visual feedback. If you need to communicate text, use `notify` instead.

    Use `stop_pet` only when the user explicitly asks to hide, stop, quit, or dismiss the pet.

    Do not notify for greetings, thanks, or purely conversational replies unless the user asks for visible pet feedback.
    """

    static func globalInstructionTargets(for kinds: [OpenPetsAgentKind]) -> [OpenPetsInstructionTarget] {
        var targets: [OpenPetsInstructionTarget] = []
        let homeDirectoryURL = FileManager.default.homeDirectoryForCurrentUser
        if kinds.contains(.codex) {
            targets.append(OpenPetsInstructionTarget(
                kind: .codex,
                displayName: "Codex global instructions",
                fileURL: homeDirectoryURL
                    .appendingPathComponent(".codex", isDirectory: true)
                    .appendingPathComponent("AGENTS.md")
            ))
        }
        if kinds.contains(.claude) {
            targets.append(OpenPetsInstructionTarget(
                kind: .claude,
                displayName: "Claude Code user instructions",
                fileURL: homeDirectoryURL
                    .appendingPathComponent(".claude", isDirectory: true)
                    .appendingPathComponent("CLAUDE.md")
            ))
        }
        if kinds.contains(.pi) {
            targets.append(OpenPetsInstructionTarget(
                kind: .pi,
                displayName: "Pi global instructions",
                fileURL: homeDirectoryURL
                    .appendingPathComponent(".pi", isDirectory: true)
                    .appendingPathComponent("agent", isDirectory: true)
                    .appendingPathComponent("AGENTS.md")
            ))
        }
        if kinds.contains(.openCode) {
            targets.append(OpenPetsInstructionTarget(
                kind: .openCode,
                displayName: "OpenCode global instructions",
                fileURL: homeDirectoryURL
                    .appendingPathComponent(".config", isDirectory: true)
                    .appendingPathComponent("opencode", isDirectory: true)
                    .appendingPathComponent("AGENTS.md")
            ))
        }
        return targets
    }

    static func appendSnippet(to fileURL: URL) throws {
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )

        let existingText = (try? String(contentsOf: fileURL, encoding: .utf8)) ?? ""
        guard !existingText.contains("## OpenPets MCP") else { return }

        let separator = existingText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "" : "\n\n"
        try (existingText + separator + snippet + "\n").write(to: fileURL, atomically: true, encoding: .utf8)
    }
}

struct OpenPetsInstructionTarget: Equatable {
    var kind: OpenPetsAgentKind
    var displayName: String
    var fileURL: URL
}

func openPetsShellQuoted(_ value: String) -> String {
    if value.isEmpty {
        return "''"
    }
    return "'\(value.replacingOccurrences(of: "'", with: "'\\''"))'"
}

private enum OpenPetsMCPJSONConfiguration {
    static func upsertHTTPServer(name: String, url: String, in configurationURL: URL) throws {
        var json = try readJSONObject(from: configurationURL)
        var servers = json["mcpServers"] as? [String: Any] ?? [:]
        var server = servers[name] as? [String: Any] ?? [:]
        server["transport"] = "streamable-http"
        server["url"] = url
        server["lifecycle"] = "eager"
        servers[name] = server
        json["mcpServers"] = servers
        try writeJSONObject(json, to: configurationURL)
    }

    static func upsertOpenCodeRemoteServer(name: String, url: String, in configurationURL: URL) throws {
        var json = try readJSONObject(from: configurationURL, allowJSONC: true)
        var servers = json["mcp"] as? [String: Any] ?? [:]
        var server = servers[name] as? [String: Any] ?? [:]
        server["type"] = "remote"
        server["url"] = url
        server["enabled"] = true
        servers[name] = server
        json["mcp"] = servers
        if json["$schema"] == nil {
            json["$schema"] = "https://opencode.ai/config.json"
        }
        try writeJSONObject(json, to: configurationURL)
    }

    static func upsertZedRemoteServer(name: String, url: String, in configurationURL: URL) throws {
        var json = try readJSONObject(from: configurationURL, allowJSONC: true)
        var servers = json["context_servers"] as? [String: Any] ?? [:]
        var server = servers[name] as? [String: Any] ?? [:]
        var headers = server["headers"] as? [String: Any] ?? [:]
        headers["Authorization"] = "Bearer openpets-local"
        server["url"] = url
        server["headers"] = headers
        servers[name] = server
        json["context_servers"] = servers
        try writeJSONObject(json, to: configurationURL)
    }

    static func removeServer(name: String, sectionKey: String, from configurationURL: URL) throws {
        var json = try readJSONObject(from: configurationURL, allowJSONC: ["context_servers", "mcp"].contains(sectionKey))
        guard var servers = json[sectionKey] as? [String: Any] else { return }
        servers.removeValue(forKey: name)
        json[sectionKey] = servers
        try writeJSONObject(json, to: configurationURL)
    }

    static func readJSONObject(from configurationURL: URL, allowJSONC: Bool = false) throws -> [String: Any] {
        guard FileManager.default.fileExists(atPath: configurationURL.path) else { return [:] }

        let data = try Data(contentsOf: configurationURL)
        guard !data.isEmpty else { return [:] }

        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            guard
                allowJSONC,
                let jsonc = String(data: data, encoding: .utf8)
            else {
                throw error
            }
            object = try JSONSerialization.jsonObject(with: Data(OpenPetsJSONC.strip(jsonc).utf8))
        }
        guard let json = object as? [String: Any] else {
            throw NSError(
                domain: "OpenPetsAgentSetup",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "\(configurationURL.path) must contain a JSON object."]
            )
        }
        return json
    }

    private static func writeJSONObject(_ json: [String: Any], to configurationURL: URL) throws {
        try FileManager.default.createDirectory(
            at: configurationURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try JSONSerialization.data(withJSONObject: json, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: configurationURL)
    }
}

private enum OpenPetsJSONC {
    static func strip(_ source: String) -> String {
        return removeTrailingCommas(from: removeComments(from: source))
    }

    private static func removeComments(from source: String) -> String {
        var output = ""
        var index = source.startIndex
        var inString = false
        var escaped = false

        while index < source.endIndex {
            let character = source[index]
            let nextIndex = source.index(after: index)
            let next = nextIndex < source.endIndex ? source[nextIndex] : nil

            if inString {
                output.append(character)
                if escaped {
                    escaped = false
                } else if character == "\\" {
                    escaped = true
                } else if character == "\"" {
                    inString = false
                }
                index = nextIndex
                continue
            }

            if character == "\"" {
                inString = true
                output.append(character)
                index = nextIndex
                continue
            }

            if character == "/", next == "/" {
                index = source.index(index, offsetBy: 2)
                while index < source.endIndex, source[index] != "\n" {
                    index = source.index(after: index)
                }
                if index < source.endIndex {
                    output.append("\n")
                    index = source.index(after: index)
                }
                continue
            }

            if character == "/", next == "*" {
                index = source.index(index, offsetBy: 2)
                while index < source.endIndex {
                    let blockNextIndex = source.index(after: index)
                    let blockNext = blockNextIndex < source.endIndex ? source[blockNextIndex] : nil
                    if source[index] == "*", blockNext == "/" {
                        index = source.index(index, offsetBy: 2)
                        break
                    }
                    if source[index] == "\n" {
                        output.append("\n")
                    }
                    index = source.index(after: index)
                }
                continue
            }

            output.append(character)
            index = nextIndex
        }

        return output
    }

    private static func removeTrailingCommas(from source: String) -> String {
        var output = ""
        var index = source.startIndex
        var inString = false
        var escaped = false

        while index < source.endIndex {
            let character = source[index]

            if inString {
                output.append(character)
                if escaped {
                    escaped = false
                } else if character == "\\" {
                    escaped = true
                } else if character == "\"" {
                    inString = false
                }
                index = source.index(after: index)
                continue
            }

            if character == "\"" {
                inString = true
                output.append(character)
                index = source.index(after: index)
                continue
            }

            if character == "," {
                var lookahead = source.index(after: index)
                while lookahead < source.endIndex, source[lookahead].isWhitespace {
                    lookahead = source.index(after: lookahead)
                }
                if lookahead < source.endIndex, source[lookahead] == "}" || source[lookahead] == "]" {
                    index = source.index(after: index)
                    continue
                }
            }

            output.append(character)
            index = source.index(after: index)
        }

        return output
    }
}
