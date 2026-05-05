import AppKit

@MainActor
final class OpenPetsAgentOnboardingWindowController: NSWindowController, NSWindowDelegate {
    private let mcpURLProvider: () -> String
    private var detector: OpenPetsAgentDetector
    private var installer: OpenPetsAgentSetupInstaller
    private var detections: [OpenPetsAgentDetection] = []
    private var rowViews: [OpenPetsAgentKind: OpenPetsAgentRowView] = [:]
    private let contentStack = NSStackView()
    private let mcpURLLabel = NSTextField(labelWithString: "")
    private let statusLabel = NSTextField(labelWithString: "")
    private let rowsStack = NSStackView()
    private let nextButton = NSButton(title: "Next", target: nil, action: nil)
    private var activeDetectionRequestID: UUID?
    private var activeInstallRequestID: UUID?

    init(
        mcpURLProvider: @escaping () -> String,
        detector: OpenPetsAgentDetector = OpenPetsAgentDetector(),
        installer: OpenPetsAgentSetupInstaller = OpenPetsAgentSetupInstaller()
    ) {
        self.mcpURLProvider = mcpURLProvider
        self.detector = detector
        self.installer = installer

        let window = NSWindow(
            contentRect: CGRect(x: 0, y: 0, width: 520, height: 480),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "Set Up OpenPets for AI Assistants"
        window.isReleasedWhenClosed = false
        window.center()

        super.init(window: window)
        window.delegate = self
        buildContent()
    }

    required init?(coder: NSCoder) {
        nil
    }

    func refreshDetections() {
        let mcpURL = mcpURLProvider()
        mcpURLLabel.stringValue = mcpURL
        statusLabel.stringValue = "Checking installed assistants..."
        setInstallControlsEnabled(false)

        let requestID = UUID()
        activeDetectionRequestID = requestID
        let detector = detector
        Task {
            let detections = await Self.detectAgents(detector: detector, mcpURL: mcpURL)
            guard self.activeDetectionRequestID == requestID else { return }
            self.activeDetectionRequestID = nil
            self.detections = detections
            self.renderDetections()
        }
    }

    private func buildContent() {
        guard let contentView = window?.contentView else { return }

        contentStack.orientation = .vertical
        contentStack.alignment = .leading
        contentStack.spacing = 14
        contentStack.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(contentStack)

        NSLayoutConstraint.activate([
            contentStack.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 20),
            contentStack.trailingAnchor.constraint(equalTo: contentView.trailingAnchor, constant: -20),
            contentStack.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 18),
            contentStack.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -18)
        ])

        showSetupScreen()
    }

    private func showSetupScreen() {
        window?.setContentSize(CGSize(width: 520, height: 480))
        replaceContentSubviews()

        let titleLabel = NSTextField(labelWithString: "Connect OpenPets to your coding agents.")
        titleLabel.font = .systemFont(ofSize: 18, weight: .semibold)
        titleLabel.lineBreakMode = .byWordWrapping

        let bodyLabel = NSTextField(labelWithString: "OpenPets will preview the MCP commands and config changes before installing them.")
        bodyLabel.font = .systemFont(ofSize: 13)
        bodyLabel.textColor = .secondaryLabelColor
        bodyLabel.lineBreakMode = .byWordWrapping
        bodyLabel.maximumNumberOfLines = 2

        let urlStack = NSStackView()
        urlStack.orientation = .horizontal
        urlStack.alignment = .firstBaseline
        urlStack.spacing = 8
        urlStack.addArrangedSubview(NSTextField(labelWithString: "MCP URL:"))
        mcpURLLabel.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        mcpURLLabel.lineBreakMode = .byTruncatingMiddle
        urlStack.addArrangedSubview(mcpURLLabel)

        rowsStack.orientation = .vertical
        rowsStack.alignment = .leading
        rowsStack.spacing = 8

        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = .secondaryLabelColor
        statusLabel.lineBreakMode = .byWordWrapping

        let footerStack = NSStackView()
        footerStack.orientation = .horizontal
        footerStack.alignment = .centerY
        footerStack.spacing = 10

        let refreshButton = NSButton(title: "Refresh", target: self, action: #selector(refreshButtonClicked))
        nextButton.target = self
        nextButton.action = #selector(nextButtonClicked)
        nextButton.bezelStyle = .rounded
        nextButton.keyEquivalent = "\r"

        footerStack.addArrangedSubview(refreshButton)
        footerStack.addArrangedSubview(NSView())
        footerStack.addArrangedSubview(nextButton)
        footerStack.setHuggingPriority(.defaultLow, for: .horizontal)

        contentStack.addArrangedSubview(titleLabel)
        contentStack.addArrangedSubview(bodyLabel)
        contentStack.addArrangedSubview(urlStack)
        contentStack.addArrangedSubview(rowsStack)
        contentStack.addArrangedSubview(statusLabel)
        contentStack.addArrangedSubview(footerStack)

        for view in [titleLabel, bodyLabel, urlStack, rowsStack, statusLabel, footerStack] {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
        }
    }

    private func showInstructionScreen() {
        window?.setContentSize(CGSize(width: 560, height: 640))
        replaceContentSubviews()

        let titleLabel = NSTextField(labelWithString: "Add OpenPets instructions.")
        titleLabel.font = .systemFont(ofSize: 18, weight: .semibold)

        let bodyLabel = NSTextField(labelWithString: "Paste this snippet into global assistant instructions so agents know when to use the OpenPets MCP tools.")
        bodyLabel.font = .systemFont(ofSize: 13)
        bodyLabel.textColor = .secondaryLabelColor
        bodyLabel.lineBreakMode = .byWordWrapping
        bodyLabel.maximumNumberOfLines = 2

        let snippetView = instructionsSnippetView(width: 520, height: 220)

        let targetStack = NSStackView()
        targetStack.orientation = .vertical
        targetStack.alignment = .leading
        targetStack.spacing = 8
        let instructionTargets = instructionTargetsForDetectedAssistants()
        if instructionTargets.isEmpty {
            let emptyLabel = NSTextField(labelWithString: "No installed assistants are available for automatic append.")
            emptyLabel.font = .systemFont(ofSize: 12)
            emptyLabel.textColor = .secondaryLabelColor
            targetStack.addArrangedSubview(emptyLabel)
        }
        for target in instructionTargets {
            let row = OpenPetsInstructionTargetRowView(target: target)
            row.appendButton.target = self
            row.appendButton.action = #selector(appendInstructionsClicked(_:))
            row.appendButton.identifier = NSUserInterfaceItemIdentifier(target.kind.executableName)
            targetStack.addArrangedSubview(row)
            row.widthAnchor.constraint(equalTo: targetStack.widthAnchor).isActive = true
        }

        statusLabel.stringValue = "Copy the snippet, or append it to a known global instructions file."

        let footerStack = NSStackView()
        footerStack.orientation = .horizontal
        footerStack.alignment = .centerY
        footerStack.spacing = 10

        let backButton = NSButton(title: "Back", target: self, action: #selector(backButtonClicked))
        let copyButton = NSButton(title: "Copy Snippet", target: self, action: #selector(copySnippetClicked))
        let closeButton = NSButton(title: "Close", target: self, action: #selector(closeButtonClicked))
        copyButton.bezelStyle = .rounded
        footerStack.addArrangedSubview(backButton)
        footerStack.addArrangedSubview(NSView())
        footerStack.addArrangedSubview(copyButton)
        footerStack.addArrangedSubview(closeButton)
        footerStack.setHuggingPriority(.defaultLow, for: .horizontal)

        contentStack.addArrangedSubview(titleLabel)
        contentStack.addArrangedSubview(bodyLabel)
        contentStack.addArrangedSubview(snippetView)
        contentStack.addArrangedSubview(targetStack)
        contentStack.addArrangedSubview(statusLabel)
        contentStack.addArrangedSubview(footerStack)

        for view in [titleLabel, bodyLabel, snippetView, targetStack, statusLabel, footerStack] {
            view.translatesAutoresizingMaskIntoConstraints = false
            view.widthAnchor.constraint(equalTo: contentStack.widthAnchor).isActive = true
        }
    }

    private func replaceContentSubviews() {
        for view in contentStack.arrangedSubviews {
            contentStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
    }

    private func renderDetections() {
        rowsStack.arrangedSubviews.forEach { view in
            rowsStack.removeArrangedSubview(view)
            view.removeFromSuperview()
        }
        rowViews.removeAll()

        for detection in detections {
            let rowView = OpenPetsAgentRowView(detection: detection)
            rowView.actionButton.target = self
            rowView.actionButton.action = #selector(rowActionClicked(_:))
            rowView.actionButton.identifier = NSUserInterfaceItemIdentifier(detection.kind.executableName)
            rowViews[detection.kind] = rowView
            rowsStack.addArrangedSubview(rowView)
            rowView.widthAnchor.constraint(equalTo: rowsStack.widthAnchor).isActive = true
        }

        statusLabel.stringValue = "Install or update each assistant one at a time."
        setInstallControlsEnabled(true)
    }

    private func setInstallControlsEnabled(_ enabled: Bool) {
        for rowView in rowViews.values {
            rowView.setControlsEnabled(enabled)
        }
        nextButton.isEnabled = enabled
    }

    @objc private func refreshButtonClicked() {
        refreshDetections()
    }

    @objc private func nextButtonClicked() {
        showInstructionScreen()
    }

    @objc private func backButtonClicked() {
        showSetupScreen()
        renderDetections()
    }

    @objc private func copySnippetClicked() {
        copyInstructionsSnippet()
        statusLabel.stringValue = "OpenPets instructions copied."
    }

    @objc private func closeButtonClicked() {
        close()
    }

    @objc private func appendInstructionsClicked(_ sender: NSButton) {
        guard
            let rawKind = sender.identifier?.rawValue,
            let kind = OpenPetsAgentKind.allCases.first(where: { $0.executableName == rawKind }),
            let target = instructionTargetsForDetectedAssistants().first(where: { $0.kind == kind })
        else { return }

        guard confirmAppendInstructions(to: target) else { return }
        do {
            try OpenPetsAssistantInstructions.appendSnippet(to: target.fileURL)
            statusLabel.stringValue = "OpenPets instructions appended to \(target.fileURL.path)."
        } catch {
            showError("Could not append instructions", detail: error.localizedDescription)
        }
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

    @objc private func rowActionClicked(_ sender: NSButton) {
        guard
            let rawKind = sender.identifier?.rawValue,
            let kind = OpenPetsAgentKind.allCases.first(where: { $0.executableName == rawKind })
        else { return }
        guard let detection = detections.first(where: { $0.kind == kind }) else { return }

        if detection.executableURL == nil {
            NSWorkspace.shared.open(kind.installGuideURL)
            return
        }

        if detection.state == .configured {
            uninstall(detection: detection)
        } else {
            install(kinds: [kind])
        }
    }

    private func install(kinds: [OpenPetsAgentKind]) {
        var selectedDetections: [OpenPetsAgentDetection] = []
        for detection in detections where kinds.contains(detection.kind) {
            if detection.executableURL != nil, detection.setupPathsAvailable {
                selectedDetections.append(detection)
            }
        }
        guard !selectedDetections.isEmpty else {
            statusLabel.stringValue = "Select an available assistant first."
            return
        }

        let mcpURL = mcpURLProvider()
        let currentInstaller = installer
        var commands: [OpenPetsAgentInstallCommand] = []
        for detection in selectedDetections {
            if let executableURL = detection.executableURL {
                commands.append(currentInstaller.command(
                    kind: detection.kind,
                    executableURL: executableURL,
                    mcpURL: mcpURL
                ))
            }
        }
        guard confirm(commands: commands, mcpURL: mcpURL) else { return }

        setInstallControlsEnabled(false)
        statusLabel.stringValue = "Installing OpenPets MCP..."
        let requestID = UUID()
        activeInstallRequestID = requestID
        let currentDetector = detector
        Task {
            let results = await Self.installAgents(
                installer: currentInstaller,
                detections: selectedDetections,
                mcpURL: mcpURL
            )
            let detections = await Self.detectAgents(detector: currentDetector, mcpURL: mcpURL)
            guard self.activeInstallRequestID == requestID else { return }
            self.activeInstallRequestID = nil
            self.detections = detections
            self.renderDetections()
            let message = results.map(\.message).joined(separator: " ")
            if results.contains(where: \.succeeded) {
                self.statusLabel.stringValue = "\(message) Click Next to add assistant instructions."
            } else {
                self.statusLabel.stringValue = message
            }
        }
    }

    private func instructionsSnippetView(width: CGFloat, height: CGFloat) -> NSView {
        let scrollView = NSScrollView(frame: CGRect(x: 0, y: 0, width: width, height: height))
        scrollView.hasVerticalScroller = true
        scrollView.borderType = .bezelBorder

        let textView = NSTextView(frame: scrollView.bounds)
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        textView.string = OpenPetsAssistantInstructions.snippet
        textView.textContainerInset = CGSize(width: 8, height: 8)
        scrollView.documentView = textView
        scrollView.heightAnchor.constraint(equalToConstant: height).isActive = true
        return scrollView
    }

    private func instructionTargetsForDetectedAssistants() -> [OpenPetsInstructionTarget] {
        let availableKinds = detections
            .filter { $0.executableURL != nil && $0.setupPathsAvailable }
            .map(\.kind)
        return OpenPetsAssistantInstructions.globalInstructionTargets(for: availableKinds)
    }

    private func copyInstructionsSnippet() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(OpenPetsAssistantInstructions.snippet, forType: .string)
    }

    private func uninstall(detection: OpenPetsAgentDetection) {
        guard detection.executableURL != nil, detection.setupPathsAvailable else {
            statusLabel.stringValue = "Select an available assistant first."
            return
        }

        let currentInstaller = installer
        guard let command = uninstallCommand(for: detection, installer: currentInstaller) else {
            statusLabel.stringValue = "Select an available assistant first."
            return
        }
        guard confirmUninstall(command: command, detection: detection) else { return }

        setInstallControlsEnabled(false)
        statusLabel.stringValue = "Removing OpenPets MCP..."
        let requestID = UUID()
        activeInstallRequestID = requestID
        let currentDetector = detector
        let mcpURL = mcpURLProvider()
        Task {
            let result = await Self.uninstallAgent(
                installer: currentInstaller,
                detection: detection
            )
            let detections = await Self.detectAgents(detector: currentDetector, mcpURL: mcpURL)
            guard self.activeInstallRequestID == requestID else { return }
            self.activeInstallRequestID = nil
            self.detections = detections
            self.renderDetections()
            self.statusLabel.stringValue = result.message
        }
    }

    private func uninstallCommand(
        for detection: OpenPetsAgentDetection,
        installer: OpenPetsAgentSetupInstaller
    ) -> OpenPetsAgentInstallCommand? {
        guard let executableURL = detection.executableURL else { return nil }
        return installer.uninstallCommand(kind: detection.kind, executableURL: executableURL)
    }

    nonisolated private static func detectAgents(
        detector: OpenPetsAgentDetector,
        mcpURL: String
    ) async -> [OpenPetsAgentDetection] {
        await Task.detached { [detector, mcpURL] in
            detector.detectAll(mcpURL: mcpURL)
        }.value
    }

    nonisolated private static func installAgents(
        installer: OpenPetsAgentSetupInstaller,
        detections: [OpenPetsAgentDetection],
        mcpURL: String
    ) async -> [OpenPetsAgentInstallResult] {
        await Task.detached { [installer, detections, mcpURL] in
            detections.map { detection in
                Self.installResult(installer: installer, detection: detection, mcpURL: mcpURL)
            }
        }.value
    }

    nonisolated private static func installResult(
        installer: OpenPetsAgentSetupInstaller,
        detection: OpenPetsAgentDetection,
        mcpURL: String
    ) -> OpenPetsAgentInstallResult {
        guard let executableURL = detection.executableURL else {
            let command = OpenPetsAgentInstallCommand(
                executableURL: URL(fileURLWithPath: detection.kind.executableName),
                arguments: []
            )
            return OpenPetsAgentInstallResult(
                kind: detection.kind,
                operation: .install,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 1,
                    standardOutput: "",
                    standardError: "Executable missing"
                )
            )
        }

        do {
            return try installer.install(kind: detection.kind, executableURL: executableURL, mcpURL: mcpURL)
        } catch {
            let command = installer.command(kind: detection.kind, executableURL: executableURL, mcpURL: mcpURL)
            return OpenPetsAgentInstallResult(
                kind: detection.kind,
                operation: .install,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 1,
                    standardOutput: "",
                    standardError: error.localizedDescription
                )
            )
        }
    }

    nonisolated private static func uninstallAgent(
        installer: OpenPetsAgentSetupInstaller,
        detection: OpenPetsAgentDetection
    ) async -> OpenPetsAgentInstallResult {
        await Task.detached { [installer, detection] in
            Self.uninstallResult(installer: installer, detection: detection)
        }.value
    }

    nonisolated private static func uninstallResult(
        installer: OpenPetsAgentSetupInstaller,
        detection: OpenPetsAgentDetection
    ) -> OpenPetsAgentInstallResult {
        guard let executableURL = detection.executableURL else {
            let command = OpenPetsAgentInstallCommand(
                executableURL: URL(fileURLWithPath: detection.kind.executableName),
                arguments: []
            )
            return OpenPetsAgentInstallResult(
                kind: detection.kind,
                operation: .uninstall,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 1,
                    standardOutput: "",
                    standardError: "Executable missing"
                )
            )
        }

        do {
            return try installer.uninstall(kind: detection.kind, executableURL: executableURL)
        } catch {
            let command = installer.uninstallCommand(kind: detection.kind, executableURL: executableURL)
            return OpenPetsAgentInstallResult(
                kind: detection.kind,
                operation: .uninstall,
                command: command,
                processResult: OpenPetsProcessResult(
                    terminationStatus: 1,
                    standardOutput: "",
                    standardError: error.localizedDescription
                )
            )
        }
    }

    private func confirm(commands: [OpenPetsAgentInstallCommand], mcpURL: String) -> Bool {
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.alertStyle = .informational
        alert.messageText = "Install OpenPets MCP?"
        alert.informativeText = """
        Active MCP URL:
        \(mcpURL)

        Commands and config changes:
        \(commands.map(\.previewText).joined(separator: "\n"))
        """
        alert.addButton(withTitle: "Install")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func confirmUninstall(
        command: OpenPetsAgentInstallCommand,
        detection: OpenPetsAgentDetection
    ) -> Bool {
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.alertStyle = .informational
        alert.messageText = "Remove OpenPets MCP from \(detection.kind.displayName)?"
        alert.informativeText = """
        Command:
        \(command.previewText)
        """
        alert.addButton(withTitle: "Remove")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }

    private func confirmAppendInstructions(to target: OpenPetsInstructionTarget) -> Bool {
        let alert = NSAlert()
        OpenPetsAppIcon.apply(to: alert)
        alert.alertStyle = .informational
        alert.messageText = "Append OpenPets instructions?"
        alert.informativeText = """
        OpenPets will append the snippet to:

        \(target.fileURL.path)
        """
        alert.addButton(withTitle: "Append")
        alert.addButton(withTitle: "Cancel")
        return alert.runModal() == .alertFirstButtonReturn
    }
}

@MainActor
private final class OpenPetsAgentRowView: NSView {
    let actionButton = NSButton(title: "", target: nil, action: nil)
    private let titleLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(labelWithString: "")
    private let statusIcon = NSImageView()

    init(detection: OpenPetsAgentDetection) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        buildLayout()
        update(detection)
    }

    required init?(coder: NSCoder) {
        nil
    }

    func setControlsEnabled(_ enabled: Bool) {
        actionButton.isEnabled = enabled
    }

    private func buildLayout() {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        let labelsStack = NSStackView()
        labelsStack.orientation = .vertical
        labelsStack.alignment = .leading
        labelsStack.spacing = 4

        titleLabel.font = .systemFont(ofSize: 14, weight: .semibold)
        detailLabel.font = .systemFont(ofSize: 12)
        detailLabel.textColor = .secondaryLabelColor
        detailLabel.lineBreakMode = .byTruncatingMiddle

        labelsStack.addArrangedSubview(titleLabel)
        labelsStack.addArrangedSubview(detailLabel)

        stack.addArrangedSubview(statusIcon)
        stack.addArrangedSubview(labelsStack)
        stack.addArrangedSubview(actionButton)
        labelsStack.setHuggingPriority(.defaultLow, for: .horizontal)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: topAnchor, constant: 10),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -10),
            statusIcon.widthAnchor.constraint(equalToConstant: 18),
            statusIcon.heightAnchor.constraint(equalToConstant: 18),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 52)
        ])

        wantsLayer = true
        layer?.cornerRadius = 8
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
    }

    private func update(_ detection: OpenPetsAgentDetection) {
        titleLabel.stringValue = "\(detection.kind.displayName) - \(stateLabel(detection.state))"
        detailLabel.stringValue = userFacingDetail(for: detection)
        statusIcon.isHidden = detection.state != .configured
        statusIcon.image = NSImage(systemSymbolName: "checkmark.circle.fill", accessibilityDescription: "Configured")
        statusIcon.contentTintColor = .systemGreen

        if detection.executableURL == nil {
            actionButton.title = "Open Guide"
        } else {
            switch detection.state {
            case .configured:
                actionButton.title = "Uninstall"
            case .missing:
                actionButton.title = "Open Guide"
            case .configuredDifferentURL:
                actionButton.title = "Update MCP"
            case .installed, .failed:
                actionButton.title = "Install MCP"
            }
        }
    }

    private func stateLabel(_ state: OpenPetsAgentSetupState) -> String {
        switch state {
        case .missing:
            "Not Installed"
        case .installed:
            "Installed"
        case .configured:
            "Configured"
        case .configuredDifferentURL:
            "Update Needed"
        case .failed:
            "Check Failed"
        }
    }

    private func userFacingDetail(for detection: OpenPetsAgentDetection) -> String {
        guard detection.setupPathsAvailable else {
            return "OpenPets cannot write the required setup files."
        }

        switch detection.state {
        case .missing:
            return "\(detection.kind.executableName) was not found."
        case .installed:
            return "Ready to install OpenPets MCP."
        case .configured:
            return "OpenPets MCP is already configured."
        case .configuredDifferentURL:
            return "Update \(detection.kind.displayName) to use this OpenPets server."
        case .failed:
            return "OpenPets could not check this assistant."
        }
    }
}

@MainActor
private final class OpenPetsInstructionTargetRowView: NSView {
    let appendButton = NSButton(title: "Append", target: nil, action: nil)
    private let titleLabel = NSTextField(labelWithString: "")
    private let pathLabel = NSTextField(labelWithString: "")

    init(target: OpenPetsInstructionTarget) {
        super.init(frame: .zero)
        translatesAutoresizingMaskIntoConstraints = false
        buildLayout()
        update(target)
    }

    required init?(coder: NSCoder) {
        nil
    }

    private func buildLayout() {
        let stack = NSStackView()
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)

        let labelsStack = NSStackView()
        labelsStack.orientation = .vertical
        labelsStack.alignment = .leading
        labelsStack.spacing = 3

        titleLabel.font = .systemFont(ofSize: 13, weight: .semibold)
        pathLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        pathLabel.textColor = .secondaryLabelColor
        pathLabel.lineBreakMode = .byTruncatingMiddle

        labelsStack.addArrangedSubview(titleLabel)
        labelsStack.addArrangedSubview(pathLabel)
        stack.addArrangedSubview(labelsStack)
        stack.addArrangedSubview(appendButton)
        labelsStack.setHuggingPriority(.defaultLow, for: .horizontal)

        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 12),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -12),
            stack.topAnchor.constraint(equalTo: topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -8),
            heightAnchor.constraint(greaterThanOrEqualToConstant: 48)
        ])

        wantsLayer = true
        layer?.cornerRadius = 8
        layer?.backgroundColor = NSColor.controlBackgroundColor.cgColor
    }

    private func update(_ target: OpenPetsInstructionTarget) {
        titleLabel.stringValue = target.displayName
        pathLabel.stringValue = target.fileURL.path
    }
}

private extension OpenPetsAgentSetupState {
    var isInstallActionable: Bool {
        switch self {
        case .installed, .configuredDifferentURL, .failed:
            true
        case .missing, .configured:
            false
        }
    }
}
