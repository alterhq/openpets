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
        controller.cleanupInstallPreview()
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

    private var appVersionMenuTitle: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String

        guard let version, !version.isEmpty else {
            return "Version Unknown"
        }

        if let build, !build.isEmpty, build != version {
            return "Version \(version) (\(build))"
        }

        return "Version \(version)"
    }

    private lazy var statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let logger = Logger(label: "openpets.menubar", factory: { StreamLogHandler.standardError(label: $0) })
    private lazy var updaterController = SPUStandardUpdaterController(
        startingUpdater: true,
        updaterDelegate: nil,
        userDriverDelegate: nil
    )
    private var configuration = OpenPetsConfiguration()
    private var petSession: OpenPetsHostSession?
    private var mcpApp: OpenPetsMCPHTTPApp?
    private var mcpTask: Task<Void, Never>?
    private var mcpState = MCPState.stopped
    private var installPreviewController: OpenPetsInstallPreviewWindowController?
    private var pendingPreparedInstall: OpenPetsPreparedInstall?
    private var activeInstallRequestID: UUID?
    private var agentOnboardingController: OpenPetsAgentOnboardingWindowController?

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
    private lazy var callPetItem = NSMenuItem(
        title: "Call my pet",
        action: #selector(callPet),
        keyEquivalent: ""
    )
    private lazy var activePetItem = NSMenuItem(
        title: "Active Pet",
        action: nil,
        keyEquivalent: ""
    )
    private lazy var installPetsItem = NSMenuItem(
        title: "Install pets...",
        action: #selector(openPetsGallery),
        keyEquivalent: ""
    )
    #if DEBUG
    private lazy var installFromLinkItem = NSMenuItem(
        title: "Install Pet From Link...",
        action: #selector(installPetFromLink),
        keyEquivalent: ""
    )
    #endif
    private lazy var openConfigItem = NSMenuItem(
        title: "Open Config Folder",
        action: #selector(openConfigFolder),
        keyEquivalent: ""
    )
    private lazy var installCommandLineToolItem = NSMenuItem(
        title: "Install CLI",
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
    private lazy var appVersionItem: NSMenuItem = {
        let item = NSMenuItem(title: appVersionMenuTitle, action: nil, keyEquivalent: "")
        item.isEnabled = false
        return item
    }()
    private lazy var quitItem = NSMenuItem(
        title: "Quit",
        action: #selector(quit),
        keyEquivalent: "q"
    )

    private struct OpenPetsMenuItems {
        var startStopServerItem: NSMenuItem
        var serverStatusItem: NSMenuItem
        var copyServerURLItem: NSMenuItem
        var wakeStopPetItem: NSMenuItem
        var callPetItem: NSMenuItem
        var activePetItem: NSMenuItem
        var installPetsItem: NSMenuItem
        var installFromLinkItem: NSMenuItem?
        var openConfigItem: NSMenuItem
        var installCommandLineToolItem: NSMenuItem
        var setUpAgentsItem: NSMenuItem
        var checkForUpdatesItem: NSMenuItem
        var appVersionItem: NSMenuItem
        var quitItem: NSMenuItem

        var targetedItems: [NSMenuItem] {
            var items = [
                startStopServerItem,
                serverStatusItem,
                copyServerURLItem,
                wakeStopPetItem,
                callPetItem,
                installPetsItem,
                openConfigItem,
                installCommandLineToolItem,
                setUpAgentsItem,
                checkForUpdatesItem,
                quitItem
            ]
            if let installFromLinkItem {
                items.append(installFromLinkItem)
            }
            return items
        }
    }

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

        let menu = makeStatusItemMenu()
        menu.delegate = self
        statusItem.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        reloadConfiguration()
        refreshMenu()
    }

    func makeStatusItemMenu() -> NSMenu {
        reloadConfiguration()
        return makeMenu(with: statusMenuItems())
    }

    func makePetContextMenu() -> NSMenu {
        reloadConfiguration()
        return makeMenu(with: makeMenuItems())
    }

    private func statusMenuItems() -> OpenPetsMenuItems {
        #if DEBUG
        let installFromLinkItem: NSMenuItem? = installFromLinkItem
        #else
        let installFromLinkItem: NSMenuItem? = nil
        #endif

        return OpenPetsMenuItems(
            startStopServerItem: startStopServerItem,
            serverStatusItem: serverStatusItem,
            copyServerURLItem: copyServerURLItem,
            wakeStopPetItem: wakeStopPetItem,
            callPetItem: callPetItem,
            activePetItem: activePetItem,
            installPetsItem: installPetsItem,
            installFromLinkItem: installFromLinkItem,
            openConfigItem: openConfigItem,
            installCommandLineToolItem: installCommandLineToolItem,
            setUpAgentsItem: setUpAgentsItem,
            checkForUpdatesItem: checkForUpdatesItem,
            appVersionItem: appVersionItem,
            quitItem: quitItem
        )
    }

    private func makeMenuItems() -> OpenPetsMenuItems {
        #if DEBUG
        let installFromLinkItem = NSMenuItem(
            title: "Install Pet From Link...",
            action: #selector(installPetFromLink),
            keyEquivalent: ""
        )
        #else
        let installFromLinkItem: NSMenuItem? = nil
        #endif

        return OpenPetsMenuItems(
            startStopServerItem: NSMenuItem(
                title: "Start MCP Server",
                action: #selector(toggleMCPServer),
                keyEquivalent: ""
            ),
            serverStatusItem: NSMenuItem(
                title: "Server Status",
                action: #selector(showServerStatus),
                keyEquivalent: ""
            ),
            copyServerURLItem: NSMenuItem(
                title: "Copy MCP URL",
                action: #selector(copyServerURL),
                keyEquivalent: ""
            ),
            wakeStopPetItem: NSMenuItem(
                title: "Wake Pet",
                action: #selector(togglePet),
                keyEquivalent: ""
            ),
            callPetItem: NSMenuItem(
                title: "Call my pet",
                action: #selector(callPet),
                keyEquivalent: ""
            ),
            activePetItem: NSMenuItem(
                title: "Active Pet",
                action: nil,
                keyEquivalent: ""
            ),
            installPetsItem: NSMenuItem(
                title: "Install pets...",
                action: #selector(openPetsGallery),
                keyEquivalent: ""
            ),
            installFromLinkItem: installFromLinkItem,
            openConfigItem: NSMenuItem(
                title: "Open Config Folder",
                action: #selector(openConfigFolder),
                keyEquivalent: ""
            ),
            installCommandLineToolItem: NSMenuItem(
                title: "Install CLI",
                action: #selector(installCommandLineTool),
                keyEquivalent: ""
            ),
            setUpAgentsItem: NSMenuItem(
                title: "Set Up AI Assistants...",
                action: #selector(setUpAgents),
                keyEquivalent: ""
            ),
            checkForUpdatesItem: NSMenuItem(
                title: "Check for Updates...",
                action: #selector(checkForUpdates),
                keyEquivalent: ""
            ),
            appVersionItem: {
                let item = NSMenuItem(title: appVersionMenuTitle, action: nil, keyEquivalent: "")
                item.isEnabled = false
                return item
            }(),
            quitItem: NSMenuItem(
                title: "Quit",
                action: #selector(quit),
                keyEquivalent: "q"
            )
        )
    }

    private func makeMenu(with items: OpenPetsMenuItems) -> NSMenu {
        for item in items.targetedItems {
            item.target = self
        }

        let menu = NSMenu()
        menu.addItem(items.startStopServerItem)
        menu.addItem(items.serverStatusItem)
        menu.addItem(items.copyServerURLItem)
        menu.addItem(.separator())
        menu.addItem(items.wakeStopPetItem)
        menu.addItem(items.callPetItem)
        menu.addItem(items.activePetItem)
        menu.addItem(items.installPetsItem)
        if let installFromLinkItem = items.installFromLinkItem {
            menu.addItem(installFromLinkItem)
        }
        menu.addItem(.separator())
        menu.addItem(items.openConfigItem)
        menu.addItem(items.installCommandLineToolItem)
        menu.addItem(items.setUpAgentsItem)
        menu.addItem(items.checkForUpdatesItem)
        menu.addItem(items.appVersionItem)
        menu.addItem(.separator())
        menu.addItem(items.quitItem)
        refreshMenuItems(items)
        return menu
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

    @objc private func callPet() {
        do {
            try wakePet()
            petSession?.callPet()
        } catch {
            showError("Could not call pet", detail: error.localizedDescription)
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

    @objc private func openPetsGallery() {
        guard let url = URL(string: "https://openpets.sh/gallery") else {
            return
        }
        NSWorkspace.shared.open(url)
    }

    @objc private func installCommandLineTool() {
        do {
            let bundledExecutableURL = try OpenPetsCommandLineToolInstaller.bundledExecutableURL()
            let installedURL = try OpenPetsCommandLineToolInstaller(
                bundledExecutableURL: bundledExecutableURL
            ).install()
            showInfo(
                "Installed CLI",
                detail: "Installed \(installedURL.path). Add \(installedURL.deletingLastPathComponent().path) to PATH if your shell does not find openpets."
            )
        } catch {
            showError("Could not install CLI", detail: error.localizedDescription)
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

    #if DEBUG
    @objc private func installPetFromLink() {
        let input = NSTextField(frame: CGRect(x: 0, y: 0, width: 420, height: 24))
        input.placeholderString = "openpets://install?url=..."
        if let pasteboardString = NSPasteboard.general.string(forType: .string) {
            input.stringValue = pasteboardString.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.messageText = "Install Pet From Link"
        alert.informativeText = "Paste an OpenPets install link or pet archive URL."
        alert.accessoryView = input
        alert.addButton(withTitle: "Install")
        alert.addButton(withTitle: "Cancel")
        NSApplication.shared.activate(ignoringOtherApps: true)
        let response = alert.runModal()

        guard response == .alertFirstButtonReturn else {
            return
        }

        let source = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !source.isEmpty, let url = URL(string: source) else {
            showError("Invalid install link", detail: "Enter a valid openpets://, http://, https://, or file:// URL.")
            return
        }

        installPet(from: url)
    }
    #endif

    func installPet(from url: URL) {
        let requestID = UUID()
        activeInstallRequestID = requestID
        cleanupInstallPreview(resetActiveRequest: false)

        Task {
            do {
                let preparedInstall = try await Task.detached {
                    try OpenPetsPetInstaller().prepare(source: url.absoluteString)
                }.value
                await MainActor.run {
                    guard self.activeInstallRequestID == requestID else {
                        preparedInstall.cleanup()
                        return
                    }
                    self.showInstallPreview(for: preparedInstall)
                }
            } catch {
                await MainActor.run {
                    guard self.activeInstallRequestID == requestID else { return }
                    self.activeInstallRequestID = nil
                    self.showError("Could not install pet", detail: error.localizedDescription)
                }
            }
        }
    }

    func cleanupInstallPreview() {
        cleanupInstallPreview(resetActiveRequest: true)
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

    private func cleanupInstallPreview(resetActiveRequest: Bool) {
        if resetActiveRequest {
            activeInstallRequestID = nil
        }
        let previewController = installPreviewController
        let preparedInstall = pendingPreparedInstall
        installPreviewController = nil
        pendingPreparedInstall = nil
        previewController?.close()
        preparedInstall?.cleanup()
    }

    private func showInstallPreview(for preparedInstall: OpenPetsPreparedInstall) {
        cleanupInstallPreview(resetActiveRequest: false)
        pendingPreparedInstall = preparedInstall

        let controller = OpenPetsInstallPreviewWindowController(preparedInstall: preparedInstall) { [weak self] action in
            self?.handleInstallPreview(action, preparedInstall: preparedInstall)
        }
        installPreviewController = controller
        controller.showWindow(nil)
        controller.window?.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    private func handleInstallPreview(
        _ action: OpenPetsInstallPreviewWindowController.Action,
        preparedInstall: OpenPetsPreparedInstall
    ) {
        guard pendingPreparedInstall == preparedInstall else {
            if action == .cancel {
                installPreviewController = nil
            }
            preparedInstall.cleanup()
            return
        }

        switch action {
        case .cancel:
            installPreviewController = nil
            pendingPreparedInstall = nil
            activeInstallRequestID = nil
            preparedInstall.cleanup()
        case .install:
            pendingPreparedInstall = nil
            activeInstallRequestID = nil
            installPreviewController?.setInstalling()
            commitPreparedInstall(preparedInstall)
        }
    }

    private func commitPreparedInstall(_ preparedInstall: OpenPetsPreparedInstall) {
        Task {
            do {
                let result = try await Task.detached {
                    defer { preparedInstall.cleanup() }
                    return try OpenPetsPetInstaller().install(prepared: preparedInstall, activate: true)
                }.value
                await MainActor.run {
                    self.configuration.activePetID = result.petID
                    if self.petSession?.isRunning == true {
                        self.stopPet()
                    }
                    do {
                        try self.wakePet()
                    } catch {
                        self.showInstallError("Installed pet, but could not load it: \(error.localizedDescription)")
                        return
                    }
                    self.refreshMenu()
                    self.finishInstallPreview()
                }
            } catch {
                preparedInstall.cleanup()
                await MainActor.run {
                    self.showInstallError("Could not install pet: \(error.localizedDescription)")
                }
            }
        }
    }

    private func finishInstallPreview() {
        let previewController = installPreviewController
        installPreviewController = nil
        pendingPreparedInstall = nil
        activeInstallRequestID = nil
        previewController?.finishAndClose()
    }

    private func showInstallError(_ message: String) {
        pendingPreparedInstall = nil
        activeInstallRequestID = nil
        if let installPreviewController {
            installPreviewController.showError(message)
        } else {
            showError("Could not install pet", detail: message)
        }
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
        let session = OpenPetsHostSession(configuration: hostConfiguration, contextMenuProvider: { [weak self] in
            self?.makePetContextMenu()
        })
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
        configuration = (try? OpenPetsConfiguration.load()) ?? OpenPetsConfiguration()
    }

    private func refreshMenu() {
        refreshMenuItems(statusMenuItems())
    }

    private func refreshMenuItems(_ items: OpenPetsMenuItems) {
        items.startStopServerItem.title = mcpState.isActive ? "Stop MCP Server" : "Start MCP Server"
        items.serverStatusItem.title = "Server Status: \(mcpState.label)"
        items.wakeStopPetItem.title = petSession?.isRunning == true ? "Stop Pet" : "Wake Pet"
        refreshPetMenu(items.activePetItem)
    }

    private func refreshPetMenu(_ activePetItem: NSMenuItem) {
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
