import AppKit
import Foundation
import Logging
import MCP
import OpenPetsCore
import Sparkle

@main
struct OpenPetsMenuBarApp {
    @MainActor
    static func main() {
        let app = NSApplication.shared
        OpenPetsAppIcon.install(on: app)
        let delegate = OpenPetsMenuBarAppDelegate()
        OpenPetsMenuBarRuntime.current = .init(delegate: delegate)
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}

@MainActor
private final class OpenPetsMenuBarRuntime {
    static var current: OpenPetsMenuBarRuntime?
    let delegate: OpenPetsMenuBarAppDelegate

    init(delegate: OpenPetsMenuBarAppDelegate) {
        self.delegate = delegate
    }
}

@MainActor
private final class OpenPetsMenuBarAppDelegate: NSObject, NSApplicationDelegate {
    private let controller = OpenPetsMenuBarController()

    func applicationDidFinishLaunching(_ notification: Foundation.Notification) {
        let shouldShowOnboarding = controller.prepareFirstLaunch()
        controller.installMenu()
        controller.startMCPServer()
        if shouldShowOnboarding {
            controller.showAgentOnboarding()
        }
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleGetURLEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    func applicationWillTerminate(_ notification: Foundation.Notification) {
        NSAppleEventManager.shared().removeEventHandler(
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
        controller.stopPet()
        controller.stopMCPServer()
    }

    @objc private func handleGetURLEvent(_ event: NSAppleEventDescriptor, withReplyEvent replyEvent: NSAppleEventDescriptor) {
        guard
            let urlString = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
            let url = URL(string: urlString)
        else {
            return
        }
        controller.installPet(from: url)
    }
}

@MainActor
final class OpenPetsMenuBarController: NSObject, NSMenuDelegate {
    private enum MCPState: Equatable {
        case stopped
        case starting
        case running
        case stopping
        case failed(String)

        var isActive: Bool {
            switch self {
            case .starting, .running:
                true
            case .stopped, .stopping, .failed:
                false
            }
        }

        var label: String {
            switch self {
            case .stopped:
                "Stopped"
            case .starting:
                "Starting"
            case .running:
                "Running"
            case .stopping:
                "Stopping"
            case .failed:
                "Failed"
            }
        }
    }

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let logger = Logger(label: "openpets.menubar", factory: { StreamLogHandler.standardError(label: $0) })
    private let updaterController = SPUStandardUpdaterController(
        startingUpdater: true,
        updaterDelegate: nil,
        userDriverDelegate: nil
    )
    private var configuration = OpenPetsConfiguration()
    private var petSession: OpenPetsHostSession?
    private var mcpApp: OpenPetsMCPHTTPApp?
    private var agentOnboardingController: OpenPetsAgentOnboardingWindowController?
    private var mcpTask: Task<Void, Never>?
    private var mcpState = MCPState.stopped

    private lazy var startStopServerItem = NSMenuItem(
        title: "Start MCP Server",
        action: #selector(toggleMCPServer),
        keyEquivalent: ""
    )
    private lazy var serverStatusItem = NSMenuItem(
        title: "Server Status",
        action: #selector(showServerStatus),
        keyEquivalent: ""
    )
    private lazy var copyServerURLItem = NSMenuItem(
        title: "Copy MCP URL",
        action: #selector(copyServerURL),
        keyEquivalent: ""
    )
    private lazy var wakeStopPetItem = NSMenuItem(
        title: "Wake Pet",
        action: #selector(togglePet),
        keyEquivalent: ""
    )
    private lazy var activePetItem = NSMenuItem(
        title: "Active Pet",
        action: nil,
        keyEquivalent: ""
    )
    private lazy var openConfigItem = NSMenuItem(
        title: "Open Config Folder",
        action: #selector(openConfigFolder),
        keyEquivalent: ""
    )
    private lazy var installCommandLineToolItem = NSMenuItem(
        title: "Install Command Line Tool",
        action: #selector(installCommandLineTool),
        keyEquivalent: ""
    )
    private lazy var setUpAgentsItem = NSMenuItem(
        title: "Set Up AI Assistants...",
        action: #selector(setUpAgents),
        keyEquivalent: ""
    )
    private lazy var checkForUpdatesItem = NSMenuItem(
        title: "Check for Updates...",
        action: #selector(checkForUpdates),
        keyEquivalent: ""
    )
    private lazy var quitItem = NSMenuItem(
        title: "Quit",
        action: #selector(quit),
        keyEquivalent: "q"
    )

    func prepareFirstLaunch() -> Bool {
        do {
            return try OpenPetsFirstLaunch.prepareConfigurationIfNeeded()
        } catch {
            showError("Could not prepare OpenPets configuration", detail: error.localizedDescription)
            return false
        }
    }

    func installMenu() {
        reloadConfiguration()
        statusItem.button?.image = NSImage(
            systemSymbolName: "pawprint.fill",
            accessibilityDescription: "OpenPets"
        )

        let menu = NSMenu()
        menu.delegate = self
        for item in [startStopServerItem, serverStatusItem, copyServerURLItem, wakeStopPetItem, openConfigItem, installCommandLineToolItem, setUpAgentsItem, checkForUpdatesItem, quitItem] {
            item.target = self
        }
        menu.addItem(startStopServerItem)
        menu.addItem(serverStatusItem)
        menu.addItem(copyServerURLItem)
        menu.addItem(.separator())
        menu.addItem(wakeStopPetItem)
        menu.addItem(activePetItem)
        menu.addItem(.separator())
        menu.addItem(openConfigItem)
        menu.addItem(installCommandLineToolItem)
        menu.addItem(setUpAgentsItem)
        menu.addItem(checkForUpdatesItem)
        menu.addItem(.separator())
        menu.addItem(quitItem)
        statusItem.menu = menu
        refreshMenu()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        reloadConfiguration()
        refreshMenu()
    }

    @objc private func toggleMCPServer() {
        if mcpState.isActive {
            stopMCPServer()
        } else {
            startMCPServer()
        }
    }

    @objc private func showServerStatus() {
        let status = statusText()
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.messageText = "OpenPets Status"
        alert.informativeText = status
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    @objc private func copyServerURL() {
        reloadConfiguration()
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(configuration.mcpClientURLString, forType: .string)
    }

    @objc private func togglePet() {
        if petSession?.isRunning == true {
            stopPet()
        } else {
            do {
                try wakePet()
            } catch {
                showError("Could not wake pet", detail: error.localizedDescription)
            }
        }
    }

    @objc private func openConfigFolder() {
        do {
            _ = try OpenPetsConfiguration.loadOrCreateDefault()
            NSWorkspace.shared.open(OpenPetsPaths.defaultConfigurationDirectory)
        } catch {
            showError("Could not open config folder", detail: error.localizedDescription)
        }
    }

    @objc private func installCommandLineTool() {
        do {
            let bundledExecutableURL = try OpenPetsCommandLineToolInstaller.bundledExecutableURL()
            let installedURL = try OpenPetsCommandLineToolInstaller(
                bundledExecutableURL: bundledExecutableURL
            ).install()
            showInfo(
                "Installed Command Line Tool",
                detail: "Installed \(installedURL.path). Add \(installedURL.deletingLastPathComponent().path) to PATH if your shell does not find openpets."
            )
        } catch {
            showError("Could not install command line tool", detail: error.localizedDescription)
        }
    }

    @objc private func setUpAgents() {
        showAgentOnboarding()
    }

    @objc private func checkForUpdates() {
        updaterController.checkForUpdates(nil)
    }

    @objc private func quit() {
        NSApplication.shared.terminate(nil)
    }

    @objc private func selectPet(_ sender: NSMenuItem) {
        guard let petID = sender.representedObject as? String else {
            return
        }

        do {
            var updatedConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            updatedConfiguration.activePetID = petID
            try updatedConfiguration.save()
            configuration = updatedConfiguration
            let shouldRestart = petSession?.isRunning == true
            if shouldRestart {
                stopPet()
                try wakePet()
            }
            refreshMenu()
        } catch {
            showError("Could not switch pet", detail: error.localizedDescription)
        }
    }

    func installPet(from url: URL) {
        Task {
            do {
                let result = try await Task.detached {
                    try OpenPetsPetInstaller().install(source: url.absoluteString)
                }.value
                await MainActor.run {
                    self.configuration.activePetID = result.petID
                    let shouldRestart = self.petSession?.isRunning == true
                    if shouldRestart {
                        self.stopPet()
                        do {
                            try self.wakePet()
                        } catch {
                            self.showError("Installed pet, but could not wake it", detail: error.localizedDescription)
                            return
                        }
                    }
                    self.refreshMenu()
                    self.showInfo("Installed \(result.displayName)", detail: result.activated ? "This pet is now active." : "The pet was installed.")
                }
            } catch {
                await MainActor.run {
                    self.showError("Could not install pet", detail: error.localizedDescription)
                }
            }
        }
    }
    func showAgentOnboarding() {
        reloadConfiguration()
        let controller = agentOnboardingController ?? OpenPetsAgentOnboardingWindowController(mcpURLProvider: { [weak self] in
            guard let self else { return OpenPetsConfiguration().mcpClientURLString }
            self.reloadConfiguration()
            return self.configuration.mcpClientURLString
        })
        agentOnboardingController = controller
        controller.refreshDetections()
        controller.showWindow(nil)
        controller.window?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func startMCPServer() {
        guard !mcpState.isActive else { return }
        reloadConfiguration()

        let config = OpenPetsMCPHTTPApp.Configuration(
            host: configuration.mcpHost,
            port: configuration.mcpPort,
            endpoint: configuration.mcpEndpoint
        )
        let app = OpenPetsMCPHTTPApp(configuration: config, serverFactory: { [weak self] _ in
            guard let self else {
                throw OpenPetsMenuBarError.controllerUnavailable
            }
            return await makeOpenPetsMCPServer(controller: self)
        }, logger: logger)

        mcpApp = app
        mcpState = .starting
        refreshMenu()

        mcpTask = Task {
            do {
                await MainActor.run {
                    self.mcpState = .running
                    self.showStartupPetGreeting()
                    self.refreshMenu()
                }
                try await app.start()
                await MainActor.run {
                    if self.mcpApp === app {
                        self.mcpApp = nil
                        self.mcpTask = nil
                        self.mcpState = .stopped
                        self.refreshMenu()
                    }
                }
            } catch {
                await MainActor.run {
                    if self.mcpApp === app {
                        self.mcpApp = nil
                        self.mcpTask = nil
                        self.mcpState = .failed(error.localizedDescription)
                        self.refreshMenu()
                        self.showError("Could not start MCP server", detail: error.localizedDescription)
                    }
                }
            }
        }
    }

    func stopMCPServer() {
        guard let app = mcpApp else { return }
        mcpState = .stopping
        refreshMenu()

        Task {
            await app.stop()
            await MainActor.run {
                if self.mcpApp === app {
                    self.mcpApp = nil
                    self.mcpTask = nil
                    self.mcpState = .stopped
                    self.refreshMenu()
                }
            }
        }
    }

    func wakePet() throws {
        if petSession?.isRunning == true {
            return
        }

        reloadConfiguration()
        let petDirectoryURL = OpenPetsPetLibrary().activePetURL(for: configuration)
        let hostConfiguration = OpenPetsHostConfiguration(
            petDirectoryURL: petDirectoryURL,
            socketPath: configuration.socketPath,
            display: configuration.display
        )
        let session = OpenPetsHostSession(configuration: hostConfiguration)
        try session.start()
        petSession = session
        refreshMenu()
    }

    func stopPet() {
        petSession?.stop()
        petSession = nil
        refreshMenu()
    }

    func wakePetForMCP() throws -> String {
        try wakePet()
        return "pet is awake"
    }

    func stopPetForMCP() -> String {
        stopPet()
        return "pet is stopped"
    }

    func notifyForMCP(_ notification: PetNotification) throws -> PetResponse {
        let wasRunning = petSession?.isRunning == true
        do {
            try wakePet()
        } catch {
            refreshMenu()
            return PetResponse(
                ok: false,
                message: petNotReadyMessage(
                    "OpenPets tried to wake the pet automatically before sending notify, but startup failed: \(error.localizedDescription)"
                )
            )
        }

        let response = sendPetCommand(.notify(notification))
        guard response.ok else {
            let attemptedAction = wasRunning
                ? "The pet was running, but notify failed"
                : "OpenPets woke the pet automatically, but notify failed"
            return PetResponse(
                ok: false,
                message: petNotReadyMessage("\(attemptedAction): \(response.message ?? "unknown error")")
            )
        }
        return response
    }

    func sendPetCommand(_ command: PetCommand) -> PetResponse {
        guard let petSession, petSession.isRunning else {
            return PetResponse(ok: false, message: "pet is not running")
        }
        let response = petSession.apply(command)
        refreshMenu()
        return response
    }

    func mcpStatusResult() -> CallTool.Result {
        let value = statusValue()
        let text = statusText()
        return CallTool.Result(
            content: [.text(text: text, annotations: nil, _meta: nil)],
            structuredContent: Optional<Value>.some(value),
            isError: false
        )
    }

    private func reloadConfiguration() {
        configuration = (try? OpenPetsConfiguration.loadOrCreateDefault()) ?? OpenPetsConfiguration()
    }

    private func refreshMenu() {
        startStopServerItem.title = mcpState.isActive ? "Stop MCP Server" : "Start MCP Server"
        serverStatusItem.title = "Server Status: \(mcpState.label)"
        wakeStopPetItem.title = petSession?.isRunning == true ? "Stop Pet" : "Wake Pet"
        refreshPetMenu()
    }

    private func refreshPetMenu() {
        let menu = NSMenu()
        let pets = OpenPetsPetLibrary().listPets()
        for pet in pets {
            let item = NSMenuItem(title: pet.displayName, action: #selector(selectPet(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = pet.id
            item.state = pet.id == configuration.activePetID ? .on : .off
            menu.addItem(item)
        }
        activePetItem.submenu = menu
        activePetItem.title = "Active Pet: \(pets.first { $0.id == configuration.activePetID }?.displayName ?? "Starcorn")"
    }

    private func showStartupPetGreeting() {
        do {
            let response = try notifyForMCP(PetNotification(
                title: "Hey there!",
                status: "message",
                ttlSeconds: 4
            ))
            if !response.ok {
                logger.warning("Could not show startup pet greeting", metadata: ["message": "\(response.message ?? "unknown")"])
            }
        } catch {
            logger.warning("Could not wake pet for startup greeting", metadata: ["error": "\(error.localizedDescription)"])
        }
    }

    private func statusText() -> String {
        let petStatus = petSession?.isRunning == true ? "Running" : "Stopped"
        let petName = petSession?.petManifest?.displayName ?? "None"
        return """
        MCP Server: \(mcpState.label)
        Endpoint: \(configuration.mcpClientURLString)
        Listening On: \(configuration.mcpListenURLString)
        Pet: \(petStatus)
        Active Pet: \(petName)
        Socket: \(configuration.socketPath)
        Config Folder: \(OpenPetsPaths.defaultConfigurationDirectory.path)
        """
    }

    private func petNotReadyMessage(_ detail: String) -> String {
        """
        Pet is not ready.
        \(detail)

        Current OpenPets status:
        \(statusText())
        """
    }

    private func statusValue() -> Value {
        var object: [String: Value] = [
            "mcpServer": .object([
                "state": .string(mcpState.label.lowercased()),
                "endpoint": .string(configuration.mcpClientURLString),
                "listenEndpoint": .string(configuration.mcpListenURLString),
                "acceptsNetworkClients": .bool(configuration.mcpAcceptsNetworkClients)
            ]),
            "pet": .object([
                "running": .bool(petSession?.isRunning == true),
                "displayName": .string(petSession?.petManifest?.displayName ?? ""),
                "id": .string(petSession?.petManifest?.id ?? "")
            ]),
            "socketPath": .string(configuration.socketPath),
            "configDirectory": .string(OpenPetsPaths.defaultConfigurationDirectory.path)
        ]

        if case .failed(let message) = mcpState {
            object["error"] = .string(message)
        }

        return .object(object)
    }

    private func showError(_ title: String, detail: String) {
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func showInfo(_ title: String, detail: String) {
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.alertStyle = .informational
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }
}

private enum OpenPetsMenuBarError: Error, LocalizedError {
    case controllerUnavailable

    var errorDescription: String? {
        switch self {
        case .controllerUnavailable:
            "OpenPets menubar controller is no longer available"
        }
    }
}

private extension OpenPetsConfiguration {
    var normalizedMCPEndpoint: String {
        mcpEndpoint.hasPrefix("/") ? mcpEndpoint : "/\(mcpEndpoint)"
    }

    var mcpListenURLString: String {
        "http://\(mcpHost.isEmpty ? "0.0.0.0" : mcpHost):\(mcpPort)\(normalizedMCPEndpoint)"
    }

    var mcpClientURLString: String {
        "http://\(mcpClientHost):\(mcpPort)\(normalizedMCPEndpoint)"
    }

    var mcpAcceptsNetworkClients: Bool {
        ["0.0.0.0", "::", ""].contains(mcpHost)
    }

    private var mcpClientHost: String {
        guard mcpAcceptsNetworkClients else {
            return mcpHost
        }

        return Host.current().localizedName.map { "\($0).local" } ?? "localhost"
    }
}
