import AppKit
import CoreGraphics
import Foundation
import ImageIO
import SwiftUI

public struct OpenPetsHostConfiguration: Sendable {
    public var petDirectoryURL: URL
    public var socketPath: String
    public var display: OpenPetsDisplayConfiguration
    public var positionStoreURL: URL

    public var scale: CGFloat {
        get { display.scale }
        set { display.scale = newValue }
    }

    public init(
        petDirectoryURL: URL,
        socketPath: String = OpenPetsPaths.defaultSocketPath,
        display: OpenPetsDisplayConfiguration = .default,
        positionStoreURL: URL = OpenPetsPaths.defaultPositionStoreURL
    ) {
        self.petDirectoryURL = petDirectoryURL
        self.socketPath = socketPath
        self.display = display
        self.positionStoreURL = positionStoreURL
    }

    public init(
        petDirectoryURL: URL,
        socketPath: String = OpenPetsPaths.defaultSocketPath,
        scale: CGFloat,
        positionStoreURL: URL = OpenPetsPaths.defaultPositionStoreURL
    ) {
        self.init(
            petDirectoryURL: petDirectoryURL,
            socketPath: socketPath,
            display: OpenPetsDisplayConfiguration(scale: scale),
            positionStoreURL: positionStoreURL
        )
    }
}

public enum OpenPetsHost {
    @MainActor
    public static func run(configuration: OpenPetsHostConfiguration) throws {
        let petBundle = try PetBundle.load(from: configuration.petDirectoryURL)
        let controller = try PetHostController(
            petBundle: petBundle,
            display: configuration.display,
            positionStore: PetPositionStore(url: configuration.positionStoreURL)
        )
        let bridge = PetHostCommandBridge(controller: controller)
        let server = OpenPetsServer(socketPath: configuration.socketPath) { command in
            bridge.handle(command)
        }
        try server.start()

        let app = NSApplication.shared
        let delegate = OpenPetsApplicationDelegate(server: server, controller: controller)
        OpenPetsRuntime.current = OpenPetsRuntime(delegate: delegate)
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        controller.show()
        app.run()
    }
}

@MainActor
private final class OpenPetsRuntime {
    static var current: OpenPetsRuntime?
    let delegate: OpenPetsApplicationDelegate

    init(delegate: OpenPetsApplicationDelegate) {
        self.delegate = delegate
    }
}

@MainActor
private final class OpenPetsApplicationDelegate: NSObject, NSApplicationDelegate {
    let server: OpenPetsServer
    let controller: PetHostController

    init(server: OpenPetsServer, controller: PetHostController) {
        self.server = server
        self.controller = controller
    }

    func applicationWillTerminate(_ notification: Notification) {
        server.stop()
        controller.savePosition()
    }
}

private final class PetHostCommandBridge: @unchecked Sendable {
    @MainActor private let controller: PetHostController

    @MainActor
    init(controller: PetHostController) {
        self.controller = controller
    }

    func handle(_ command: PetCommand) -> PetResponse {
        switch command {
        case .ping:
            return PetResponse(ok: true, message: "pong")
        case .shutdown:
            DispatchQueue.main.async { [self] in
                Task { @MainActor in
                    controller.savePosition()
                    NSApplication.shared.terminate(nil)
                }
            }
            return PetResponse(ok: true, message: "shutting down")
        default:
            DispatchQueue.main.async { [self] in
                Task { @MainActor in
                    controller.apply(command)
                }
            }
            return PetResponse(ok: true)
        }
    }
}

@MainActor
private final class PetHostController {
    private let petBundle: PetBundle
    private let positionStore: PetPositionStore
    private let window: NSPanel
    private let petView: PetHostView
    private let messageAreaHeight: CGFloat
    private var animationTimer: Timer?
    private var ttlWorkItem: DispatchWorkItem?
    private var messageWorkItem: DispatchWorkItem?
    private var currentAnimation: PetAnimation = .idle
    private var currentFrameIndex = 0
    private var remainingAnimationCycles: Int?

    init(petBundle: PetBundle, display: OpenPetsDisplayConfiguration, positionStore: PetPositionStore) throws {
        self.petBundle = petBundle
        self.positionStore = positionStore
        messageAreaHeight = max(display.messageAreaHeight, 84)

        let frames = try PetHostController.loadFrames(from: petBundle)
        let spriteSize = CGSize(
            width: CGFloat(petBundle.atlas.cellWidth) * display.scale,
            height: CGFloat(petBundle.atlas.cellHeight) * display.scale
        )
        let contentSize = CGSize(
            width: max(316, spriteSize.width + 120),
            height: spriteSize.height + messageAreaHeight
        )
        petView = PetHostView(
            frame: CGRect(origin: .zero, size: contentSize),
            spriteSize: spriteSize,
            messageAreaHeight: messageAreaHeight,
            frames: frames
        )

        let initialOrigin = positionStore.loadPosition(forPetID: petBundle.manifest.id)
            ?? PetHostController.defaultWindowOrigin(contentSize: contentSize)
        window = NSPanel(
            contentRect: CGRect(origin: initialOrigin, size: contentSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        window.backgroundColor = .clear
        window.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        window.contentView = petView
        window.hasShadow = false
        window.ignoresMouseEvents = false
        window.isMovableByWindowBackground = false
        window.isOpaque = false
        window.level = .statusBar

        petView.onClick = { [weak self] in
            self?.play(.waving, loop: false, ttlSeconds: nil)
        }
        petView.onDragDirectionChange = { [weak self] direction in
            self?.switchDragDirection(to: direction)
        }
        petView.onDragEnd = { [weak self] in
            self?.savePosition()
            self?.play(.idle, loop: true, ttlSeconds: nil)
        }

        play(.idle, loop: true, ttlSeconds: nil)
    }

    func show() {
        window.orderFrontRegardless()
    }

    func apply(_ command: PetCommand) {
        switch command {
        case .setMessage(let text, let ttlSeconds, _):
            setMessage(text, ttlSeconds: ttlSeconds)
        case .setStatus(let kind, let message, let ttlSeconds):
            setBubble(bubble(forStatusKind: kind, message: message), ttlSeconds: ttlSeconds)
            if let finiteAnimation = finiteAnimation(forStatusKind: kind) {
                play(finiteAnimation, loopCount: 3, ttlSeconds: nil)
            } else {
                play(animation(forStatusKind: kind), loop: true, ttlSeconds: ttlSeconds)
            }
        case .playAnimation(let name, let loop, let ttlSeconds):
            play(name, loop: loop ?? true, ttlSeconds: ttlSeconds)
        case .clearMessage:
            setBubble(nil, ttlSeconds: nil)
        case .ping, .shutdown:
            break
        }
    }

    func savePosition() {
        try? positionStore.savePosition(window.frame.origin, forPetID: petBundle.manifest.id)
    }

    private func setMessage(_ text: String?, ttlSeconds: Double?) {
        setBubble(bubble(forMessage: text), ttlSeconds: ttlSeconds)
    }

    private func setBubble(_ bubble: PetBubble?, ttlSeconds: Double?) {
        messageWorkItem?.cancel()
        petView.bubble = bubble

        guard let ttlSeconds, ttlSeconds > 0, bubble != nil else { return }
        let workItem = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                self?.petView.bubble = nil
            }
        }
        messageWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + ttlSeconds, execute: workItem)
    }

    private func play(_ animation: PetAnimation, loop: Bool, ttlSeconds: Double?) {
        play(animation, loopCount: loop ? nil : 1, ttlSeconds: ttlSeconds)
    }

    private func play(_ animation: PetAnimation, loopCount: Int?, ttlSeconds: Double?) {
        ttlWorkItem?.cancel()
        currentAnimation = animation
        currentFrameIndex = entryFrame(for: animation)
        remainingAnimationCycles = loopCount
        petView.set(animation: animation, frameIndex: currentFrameIndex)
        scheduleNextFrame()

        guard let ttlSeconds, ttlSeconds > 0, animation != .idle else { return }
        let workItem = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                self?.play(.idle, loop: true, ttlSeconds: nil)
            }
        }
        ttlWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + ttlSeconds, execute: workItem)
    }

    private func switchDragDirection(to animation: PetAnimation) {
        ttlWorkItem?.cancel()
        let previousAnimation = currentAnimation
        currentAnimation = animation
        remainingAnimationCycles = nil
        if previousAnimation == .runningRight || previousAnimation == .runningLeft {
            currentFrameIndex %= animation.frameCount
        } else {
            currentFrameIndex = entryFrame(for: animation)
        }
        petView.set(animation: animation, frameIndex: currentFrameIndex)
        scheduleNextFrame()
    }

    private func entryFrame(for animation: PetAnimation) -> Int {
        switch animation {
        case .runningRight, .runningLeft:
            1
        case .waving, .jumping, .failed:
            min(1, animation.frameCount - 1)
        case .idle, .waiting, .running, .review:
            0
        }
    }

    private func scheduleNextFrame() {
        animationTimer?.invalidate()
        let durations = currentAnimation.frameDurationsMilliseconds
        let duration = Double(durations[min(currentFrameIndex, durations.count - 1)]) / 1000
        animationTimer = Timer.scheduledTimer(withTimeInterval: duration, repeats: false) { [weak self] _ in
            Task { @MainActor in
                self?.advanceFrame()
            }
        }
    }

    private func advanceFrame() {
        let frameCount = currentAnimation.frameCount
        currentFrameIndex += 1

        if currentFrameIndex >= frameCount {
            if let remainingAnimationCycles {
                let remainingCycles = remainingAnimationCycles - 1
                if remainingCycles > 0 {
                    self.remainingAnimationCycles = remainingCycles
                    currentFrameIndex = 0
                } else {
                    play(.idle, loop: true, ttlSeconds: nil)
                    return
                }
            } else {
                currentFrameIndex = 0
            }
        }

        petView.set(animation: currentAnimation, frameIndex: currentFrameIndex)
        scheduleNextFrame()
    }

    private func animation(forStatusKind kind: String) -> PetAnimation {
        switch kind.lowercased() {
        case "failed", "failure", "error":
            .failed
        case "review", "reviewing":
            .review
        case "waiting", "queued", "pending":
            .waiting
        case "running", "task", "working":
            .running
        case "done", "success", "completed", "complete", "committed":
            .jumping
        case "attention", "reply", "message":
            .waving
        default:
            .idle
        }
    }

    private func finiteAnimation(forStatusKind kind: String) -> PetAnimation? {
        switch kind.lowercased() {
        case "done", "success", "completed", "complete", "committed":
            .jumping
        case "review", "reviewing":
            .review
        case "running", "task", "working":
            .running
        default:
            nil
        }
    }

    private func bubble(forMessage text: String?) -> PetBubble? {
        guard let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }

        let lines = text
            .split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
        if lines.count > 1, !lines[1].isEmpty {
            return PetBubble(title: lines[0], detail: lines[1], indicator: .working)
        }

        return PetBubble(title: petBundle.manifest.displayName, detail: text, indicator: .working)
    }

    private func bubble(forStatusKind kind: String, message: String?) -> PetBubble {
        let normalized = kind.lowercased()
        let title: String
        let indicator: PetBubbleIndicator

        switch normalized {
        case "done", "success", "completed", "complete", "committed":
            title = "Complete"
            indicator = .success
        case "failed", "failure", "error":
            title = "Needs attention"
            indicator = .working
        case "review", "reviewing":
            title = "Reviewing"
            indicator = .working
        case "running", "task", "working":
            title = "Working"
            indicator = .working
        default:
            title = kind.isEmpty ? petBundle.manifest.displayName : kind.capitalized
            indicator = .working
        }

        return PetBubble(title: title, detail: message, indicator: indicator)
    }

    private static func defaultWindowOrigin(contentSize: CGSize) -> CGPoint {
        let screenFrame = NSScreen.main?.visibleFrame ?? CGRect(x: 0, y: 0, width: 1280, height: 800)
        return CGPoint(
            x: screenFrame.maxX - contentSize.width - 40,
            y: screenFrame.minY + 40
        )
    }

    private static func loadFrames(from petBundle: PetBundle) throws -> [PetAnimation: [CGImage]] {
        guard
            let source = CGImageSourceCreateWithURL(petBundle.spritesheetURL as CFURL, nil),
            let spritesheet = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            throw OpenPetsError.invalidSpritesheet(petBundle.spritesheetURL)
        }

        var frames: [PetAnimation: [CGImage]] = [:]
        for animation in PetAnimation.allCases {
            let row = animation.row
            frames[animation] = (0..<animation.frameCount).compactMap { column in
                let rect = CGRect(
                    x: column * petBundle.atlas.cellWidth,
                    y: row * petBundle.atlas.cellHeight,
                    width: petBundle.atlas.cellWidth,
                    height: petBundle.atlas.cellHeight
                )
                return spritesheet.cropping(to: rect)
            }
        }

        return frames
    }
}

private struct PetBubble {
    var title: String
    var detail: String?
    var indicator: PetBubbleIndicator
}

private enum PetBubbleIndicator {
    case working
    case success
}

@MainActor
private final class PetHostView: NSView {
    var onClick: (() -> Void)? {
        get { spriteView.onClick }
        set { spriteView.onClick = newValue }
    }

    var onDragDirectionChange: ((PetAnimation) -> Void)? {
        get { spriteView.onDragDirectionChange }
        set { spriteView.onDragDirectionChange = newValue }
    }

    var onDragEnd: (() -> Void)? {
        get { spriteView.onDragEnd }
        set { spriteView.onDragEnd = newValue }
    }

    var bubble: PetBubble? {
        didSet {
            if bubble == nil {
                isMessageCollapsed = false
                activeMessageCount = 0
            } else if oldValue == nil || !isMessageCollapsed {
                activeMessageCount = 1
            } else {
                activeMessageCount += 1
            }
            if let bubble, !bounds.isEmpty {
                let size = resizeWindowToPreferredSize(for: bubble)
                currentMessageLayout = messageLayout(for: bubble, containerSize: size)
            } else if bubble == nil {
                _ = resizeWindowToPreferredSize(for: nil)
            }
            updateMessageView()
            needsLayout = true
        }
    }

    private let spriteView: PetSpriteView
    private lazy var bubbleView: MessageHostingView = {
        let view = MessageHostingView(rootView: OpenPetsMessageView(
            bubble: nil,
            isCollapsed: false,
            activeMessageCount: 0,
            layout: .empty,
            onToggle: {}
        ))
        return view
    }()
    private let spriteSize: CGSize
    private let messageAreaHeight: CGFloat
    private let compactSize: CGSize
    private var isMessageCollapsed = false
    private var activeMessageCount = 0
    private var currentMessageLayout = OpenPetsMessageLayout.empty
    private var mouseDownInsideToggle = false

    init(
        frame: CGRect,
        spriteSize: CGSize,
        messageAreaHeight: CGFloat,
        frames: [PetAnimation: [CGImage]]
    ) {
        self.spriteSize = spriteSize
        self.messageAreaHeight = messageAreaHeight
        compactSize = frame.size
        spriteView = PetSpriteView(frame: frame, spriteSize: spriteSize, frames: frames)
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
        addSubview(spriteView)
        bubbleView.wantsLayer = true
        bubbleView.layer?.backgroundColor = NSColor.clear.cgColor
        bubbleView.isHidden = true
        addSubview(bubbleView)
    }

    required init?(coder: NSCoder) {
        nil
    }

    override var isOpaque: Bool {
        false
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        if containsToggleHit(point) {
            return self
        }
        return super.hitTest(point)
    }

    override func mouseDown(with event: NSEvent) {
        mouseDownInsideToggle = containsToggleHit(convert(event.locationInWindow, from: nil))
    }

    override func mouseUp(with event: NSEvent) {
        let point = convert(event.locationInWindow, from: nil)
        defer { mouseDownInsideToggle = false }

        if mouseDownInsideToggle, containsToggleHit(point) {
            toggleMessageCollapsed()
            return
        }

        super.mouseUp(with: event)
    }

    override func layout() {
        super.layout()
        guard let bubble else {
            spriteView.frame = bounds
            spriteView.spriteFrame = defaultSpriteFrame(in: bounds)
            bubbleView.frame = .zero
            bubbleView.interactiveRect = .zero
            currentMessageLayout = .empty
            return
        }
        currentMessageLayout = messageLayout(for: bubble, containerSize: bounds.size)
        spriteView.frame = bounds
        spriteView.spriteFrame = currentMessageLayout.spriteFrame
        bubbleView.frame = bounds
        bubbleView.interactiveRect = currentMessageLayout.toggleFrame
        updateMessageView(layout: currentMessageLayout)
    }

    func set(animation: PetAnimation, frameIndex: Int) {
        spriteView.set(animation: animation, frameIndex: frameIndex)
    }

    private func messageLayout(for bubble: PetBubble, containerSize: CGSize) -> OpenPetsMessageLayout {
        OpenPetsMessageLayout.make(
            bubble: bubble,
            isCollapsed: isMessageCollapsed,
            containerWidth: containerSize.width,
            spriteSize: spriteSize,
            messageAreaHeight: messageAreaHeight
        )
    }

    private func preferredSize(for bubble: PetBubble?) -> CGSize {
        guard let bubble else {
            return compactSize
        }
        return messageLayout(for: bubble, containerSize: compactSize).containerSize
    }

    @discardableResult
    private func resizeWindowToPreferredSize(for bubble: PetBubble?) -> CGSize {
        let size = preferredSize(for: bubble)
        guard abs(size.width - bounds.width) > 0.5 || abs(size.height - bounds.height) > 0.5 else {
            return size
        }

        if let window {
            var frame = window.frame
            frame.size = size
            window.setFrame(frame, display: false)
        } else {
            setFrameSize(size)
        }
        return size
    }

    private func defaultSpriteFrame(in bounds: CGRect) -> CGRect {
        CGRect(
            x: bounds.width - spriteSize.width - OpenPetsMessageLayout.sideInset,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
    }

    private func containsToggleHit(_ point: NSPoint) -> Bool {
        guard bubble != nil else { return false }
        let normalizedPoint = isFlipped
            ? CGPoint(x: point.x, y: bounds.height - point.y)
            : point
        return currentMessageLayout.toggleFrame.contains(normalizedPoint)
    }

    private func toggleMessageCollapsed() {
        guard bubble != nil else { return }
        isMessageCollapsed.toggle()
        if !isMessageCollapsed {
            activeMessageCount = 1
        }
        if let bubble, !bounds.isEmpty {
            let size = resizeWindowToPreferredSize(for: bubble)
            currentMessageLayout = messageLayout(for: bubble, containerSize: size)
        }
        updateMessageView(layout: currentMessageLayout)
        needsLayout = true
    }

    private func updateMessageView(layout: OpenPetsMessageLayout? = nil) {
        let layout = layout ?? currentMessageLayout
        bubbleView.rootView = OpenPetsMessageView(
            bubble: bubble,
            isCollapsed: isMessageCollapsed,
            activeMessageCount: activeMessageCount,
            layout: layout,
            onToggle: { [weak self] in
                self?.toggleMessageCollapsed()
            }
        )
        bubbleView.isHidden = bubble == nil
    }
}

private struct OpenPetsMessageLayout {
    static let toggleDiameter: CGFloat = 36
    static let verticalGap: CGFloat = 10
    static let toggleGapBelowCard: CGFloat = 4
    static let sideInset: CGFloat = 12
    static let maxCardWidth: CGFloat = 260
    static let empty = OpenPetsMessageLayout(
        containerSize: .zero,
        cardFrame: .zero,
        spriteFrame: .zero,
        toggleFrame: .zero
    )

    var containerSize: CGSize
    var cardFrame: CGRect
    var spriteFrame: CGRect
    var toggleFrame: CGRect

    @MainActor
    static func make(
        bubble: PetBubble,
        isCollapsed: Bool,
        containerWidth: CGFloat,
        spriteSize: CGSize,
        messageAreaHeight: CGFloat
    ) -> OpenPetsMessageLayout {
        let cardMaxWidth = min(maxCardWidth, max(1, containerWidth - sideInset * 2))
        let cardSize = OpenPetsBubbleContentView.size(
            for: bubble,
            maxWidth: cardMaxWidth,
            messageAreaHeight: messageAreaHeight
        )
        let rightEdge = sideInset + cardSize.width
        let spriteFrame = CGRect(
            x: rightEdge - spriteSize.width,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
        let cardFrame = CGRect(
            x: rightEdge - cardSize.width,
            y: spriteFrame.maxY + verticalGap,
            width: cardSize.width,
            height: cardSize.height
        )
        let toggleFrame = CGRect(
            x: rightEdge - toggleDiameter,
            y: cardFrame.minY - toggleDiameter - toggleGapBelowCard,
            width: toggleDiameter,
            height: toggleDiameter
        )
        let contentHeight = isCollapsed
            ? max(spriteSize.height + toggleDiameter / 2, spriteSize.height)
            : spriteSize.height + cardSize.height + verticalGap

        return OpenPetsMessageLayout(
            containerSize: CGSize(width: containerWidth, height: contentHeight),
            cardFrame: cardFrame,
            spriteFrame: spriteFrame,
            toggleFrame: toggleFrame
        )
    }
}

private final class MessageHostingView: NSHostingView<OpenPetsMessageView> {
    var interactiveRect = CGRect.zero

    override func hitTest(_ point: NSPoint) -> NSView? {
        guard containsToggle(point) else { return nil }
        return super.hitTest(point)
    }

    private func containsToggle(_ point: NSPoint) -> Bool {
        let normalizedPoint = isFlipped
            ? CGPoint(x: point.x, y: bounds.height - point.y)
            : point
        return interactiveRect.contains(normalizedPoint)
    }
}

private struct OpenPetsMessageView: View {
    let bubble: PetBubble?
    let isCollapsed: Bool
    let activeMessageCount: Int
    let layout: OpenPetsMessageLayout
    let onToggle: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Group {
            if let bubble {
                ZStack(alignment: .topLeading) {
                    if !isCollapsed {
                        OpenPetsBubbleContentView(bubble: bubble)
                            .position(swiftUIPosition(for: layout.cardFrame))
                    }
                    toggleButton
                        .position(swiftUIPosition(for: layout.toggleFrame))
                }
                .frame(
                    width: layout.containerSize.width,
                    height: layout.containerSize.height,
                    alignment: .topLeading
                )
            } else {
                Color.clear.frame(width: 0, height: 0)
            }
        }
    }

    private func swiftUIPosition(for frame: CGRect) -> CGPoint {
        CGPoint(
            x: frame.midX,
            y: layout.containerSize.height - frame.midY
        )
    }

    private var toggleButton: some View {
        Button(action: onToggle) {
            ZStack {
                Circle()
                    .fill(Color(nsColor: .controlBackgroundColor).opacity(colorScheme == .dark ? 0.96 : 0.98))
                Circle()
                    .stroke(Color(nsColor: .separatorColor).opacity(colorScheme == .dark ? 0.6 : 0.35), lineWidth: 1)

                if isCollapsed {
                    Text("\(min(max(activeMessageCount, 1), 99))")
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .foregroundStyle(.primary)
                } else {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }
            .frame(width: OpenPetsMessageLayout.toggleDiameter, height: OpenPetsMessageLayout.toggleDiameter)
            .contentShape(Circle())
            .shadow(color: .black.opacity(colorScheme == .dark ? 0.24 : 0.08), radius: 3, x: 0, y: 1)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isCollapsed ? "Show messages" : "Hide messages")
        .accessibilityValue(isCollapsed ? "\(activeMessageCount) active" : "")
    }
}

private struct OpenPetsBubbleContentView: View {
    let bubble: PetBubble
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(alignment: .leading, spacing: 1) {
                Text(bubble.title)
                    .font(.system(size: 13.5, weight: .semibold))
                    .foregroundStyle(.primary)
                    .lineLimit(1)
                    .truncationMode(.tail)

                if let detail = bubble.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.system(size: 12.5, weight: .regular))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                        .truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: false)
                }
            }

            Spacer(minLength: 4)
            indicator(for: bubble.indicator)
                .frame(width: 16, height: 16)
        }
        .padding(.leading, 14)
        .padding(.trailing, 12)
        .padding(.vertical, 6)
        .frame(width: Self.size(for: bubble).width, height: Self.size(for: bubble).height)
        .background(background)
        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: 22, style: .continuous)
                .stroke(Color(nsColor: .separatorColor).opacity(colorScheme == .dark ? 0.55 : 0.35), lineWidth: 1)
        }
        .shadow(color: .black.opacity(colorScheme == .dark ? 0.28 : 0.10), radius: 4, x: 0, y: 1)
    }

    static func size(for bubble: PetBubble, maxWidth: CGFloat = 260, messageAreaHeight: CGFloat = 84) -> CGSize {
        let width = min(260, maxWidth)
        let maxHeight = messageAreaHeight - 12
        guard let detail = bubble.detail, !detail.isEmpty else {
            return CGSize(width: width, height: min(maxHeight, 44))
        }

        let bodyLineCount = measuredBodyLineCount(for: detail, bubbleWidth: width)
        let oneLineBodyHeight: CGFloat = 56
        let bodyLineHeight: CGFloat = 16
        let desiredHeight = oneLineBodyHeight + CGFloat(bodyLineCount - 1) * bodyLineHeight
        return CGSize(
            width: width,
            height: min(maxHeight, desiredHeight)
        )
    }

    private static func measuredBodyLineCount(for detail: String, bubbleWidth: CGFloat) -> Int {
        let bodyWidth = max(1, bubbleWidth - 54)
        let font = NSFont.systemFont(ofSize: 12.5, weight: .regular)
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineBreakMode = .byWordWrapping
        let rect = NSString(string: detail).boundingRect(
            with: CGSize(width: bodyWidth, height: .greatestFiniteMagnitude),
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: [
                .font: font,
                .paragraphStyle: paragraph
            ]
        )
        let bodyLineHeight: CGFloat = 15
        return min(2, max(1, Int(ceil((rect.height - 0.5) / bodyLineHeight))))
    }

    private var background: some View {
        Color(nsColor: .controlBackgroundColor)
            .opacity(colorScheme == .dark ? 0.92 : 0.96)
    }

    @ViewBuilder
    private func indicator(for indicator: PetBubbleIndicator) -> some View {
        switch indicator {
        case .working:
            ProgressView()
                .scaleEffect(0.5)
                .opacity(0.7)
        case .success:
            ZStack {
                Circle()
                    .fill(Color(nsColor: .systemGreen))
                Image(systemName: "checkmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
            }
        }
    }
}

private struct WorkingProgressRing: View {
    var body: some View {
        TimelineView(.animation) { timeline in
            let phase = timeline.date.timeIntervalSinceReferenceDate
                .truncatingRemainder(dividingBy: 1.2) / 1.2
            let progress = 0.12 + phase * 0.76

            ProgressView(value: progress, total: 1.0)
                .progressViewStyle(.circular)
                .controlSize(.small)
                .scaleEffect(0.7)
                .frame(width: 16, height: 16)
        }
    }
}

@MainActor
private final class PetSpriteView: NSView {
    var onClick: (() -> Void)?
    var onDragDirectionChange: ((PetAnimation) -> Void)?
    var onDragEnd: (() -> Void)?
    var spriteFrame: CGRect {
        didSet {
            needsDisplay = true
        }
    }

    private let spriteSize: CGSize
    private let frames: [PetAnimation: [CGImage]]
    private var currentFrame: CGImage?
    private var mouseDownScreenLocation = CGPoint.zero
    private var previousDragScreenLocation = CGPoint.zero
    private var mouseDownWindowOrigin = CGPoint.zero
    private var dragging = false
    private var lastDragAnimation: PetAnimation?

    init(
        frame: CGRect,
        spriteSize: CGSize,
        frames: [PetAnimation: [CGImage]]
    ) {
        self.spriteSize = spriteSize
        self.frames = frames
        spriteFrame = CGRect(
            x: frame.width - spriteSize.width - OpenPetsMessageLayout.sideInset,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
        currentFrame = frames[.idle]?.first
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
    }

    required init?(coder: NSCoder) {
        nil
    }

    override var isOpaque: Bool {
        false
    }

    func set(animation: PetAnimation, frameIndex: Int) {
        guard let animationFrames = frames[animation], !animationFrames.isEmpty else { return }
        currentFrame = animationFrames[frameIndex % animationFrames.count]
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.setFill()
        dirtyRect.fill()

        guard let currentFrame else { return }
        NSGraphicsContext.current?.imageInterpolation = .none
        NSImage(cgImage: currentFrame, size: spriteSize).draw(in: spriteFrame)
    }

    override func mouseDown(with event: NSEvent) {
        mouseDownScreenLocation = NSEvent.mouseLocation
        previousDragScreenLocation = mouseDownScreenLocation
        mouseDownWindowOrigin = window?.frame.origin ?? .zero
        dragging = false
        lastDragAnimation = nil
    }

    override func mouseDragged(with event: NSEvent) {
        guard let window else { return }
        let currentLocation = NSEvent.mouseLocation
        let delta = CGPoint(
            x: currentLocation.x - mouseDownScreenLocation.x,
            y: currentLocation.y - mouseDownScreenLocation.y
        )

        if !dragging, hypot(delta.x, delta.y) > 4 {
            dragging = true
        }

        window.setFrameOrigin(CGPoint(
            x: mouseDownWindowOrigin.x + delta.x,
            y: mouseDownWindowOrigin.y + delta.y
        ))

        let incrementalX = currentLocation.x - previousDragScreenLocation.x
        previousDragScreenLocation = currentLocation

        guard abs(incrementalX) > 0.5 else { return }
        let animation: PetAnimation = incrementalX >= 0 ? .runningRight : .runningLeft
        if animation != lastDragAnimation {
            lastDragAnimation = animation
            onDragDirectionChange?(animation)
        }
    }

    override func mouseUp(with event: NSEvent) {
        if dragging {
            onDragEnd?()
        } else {
            onClick?()
        }
    }

}

#if DEBUG
private struct OpenPetsMessagingPreviewGallery: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            preview(
                "Title Only",
                bubble: PetBubble(
                    title: "Waiting",
                    detail: nil,
                    indicator: .working
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "One Body Line",
                bubble: PetBubble(
                    title: "Working",
                    detail: "Updating the interface.",
                    indicator: .working
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "One Body Line Success",
                bubble: PetBubble(
                    title: "Complete",
                    detail: "Layout is ready for review.",
                    indicator: .success
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "Two Body Lines",
                bubble: PetBubble(
                    title: "Describe project",
                    detail: "This project is OpenPets, a macOS Swift package for showing an animated desktop pet while work runs.",
                    indicator: .working
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "Two Lines Truncated",
                bubble: PetBubble(
                    title: "Summarize implementation",
                    detail: "The bubble should expand to two lines and then truncate any extra copy with an ellipsis so the card stays compact.",
                    indicator: .working
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "Collapsed Active",
                bubble: PetBubble(
                    title: "Describe project",
                    detail: "No. I checked for CLAUDE.md, AGENTS.md, AGENT.md, .agents.md,...",
                    indicator: .success
                ),
                isCollapsed: true,
                activeMessageCount: 1,
                appearance: .aqua
            )
            preview(
                "Collapsed Multiple",
                bubble: PetBubble(
                    title: "Working",
                    detail: "Three active updates are hidden.",
                    indicator: .working
                ),
                isCollapsed: true,
                activeMessageCount: 3,
                appearance: .aqua
            )
            preview(
                "Dark Two Lines Truncated",
                bubble: PetBubble(
                    title: "Needs attention",
                    detail: "Longer status copy wraps cleanly without crowding the indicator, even when there is more detail than the bubble can show.",
                    indicator: .working
                ),
                isCollapsed: false,
                activeMessageCount: 1,
                appearance: .darkAqua
            )
            preview(
                "Dark Collapsed Multiple",
                bubble: PetBubble(
                    title: "Needs attention",
                    detail: "Hidden status copy.",
                    indicator: .working
                ),
                isCollapsed: true,
                activeMessageCount: 12,
                appearance: .darkAqua
            )
        }
        .padding(16)
        .frame(width: 324)
    }

    private func preview(
        _ title: String,
        bubble: PetBubble,
        isCollapsed: Bool,
        activeMessageCount: Int,
        appearance: NSAppearance.Name
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            let layout = previewLayout(for: bubble, isCollapsed: isCollapsed)
            ZStack(alignment: .topLeading) {
                PreviewStarcornSprite()
                    .position(
                        x: layout.spriteFrame.midX,
                        y: layout.containerSize.height - layout.spriteFrame.midY
                    )
                OpenPetsMessageView(
                    bubble: bubble,
                    isCollapsed: isCollapsed,
                    activeMessageCount: activeMessageCount,
                    layout: layout,
                    onToggle: {}
                )
            }
            .frame(width: layout.containerSize.width, height: layout.containerSize.height)
            .background(Color(nsColor: .windowBackgroundColor))
            .environment(\.colorScheme, appearance == .darkAqua ? .dark : .light)
        }
    }

    private var previewCanvasSize: CGSize {
        CGSize(width: 316, height: 190)
    }

    private func previewLayout(for bubble: PetBubble, isCollapsed: Bool) -> OpenPetsMessageLayout {
        OpenPetsMessageLayout.make(
            bubble: bubble,
            isCollapsed: isCollapsed,
            containerWidth: previewCanvasSize.width,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 84
        )
    }
}

private struct PreviewStarcornSprite: View {
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Group {
            if let image = Self.idleFrame {
                Image(decorative: image, scale: 1)
                    .interpolation(.none)
                    .resizable()
                    .scaledToFit()
            } else {
                RoundedRectangle(cornerRadius: 26, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
                    .overlay {
                        Text("Starcorn")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
            }
        }
        .frame(width: 112, height: 126)
        .opacity(colorScheme == .dark ? 0.82 : 1)
    }

    private static let idleFrame: CGImage? = {
        guard
            let spritesheetURL = OpenPetsPreviewResources.starcornResourceURL(
                named: "spritesheet",
                extension: "webp"
            ),
            let source = CGImageSourceCreateWithURL(spritesheetURL as CFURL, nil),
            let spritesheet = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else {
            return nil
        }

        let columns = 8
        let rows = 9
        let cellWidth = spritesheet.width / columns
        let cellHeight = spritesheet.height / rows
        return spritesheet.cropping(to: CGRect(x: 0, y: 0, width: cellWidth, height: cellHeight))
    }()
}

#Preview("Starcorn Sprite Resource") {
    PreviewStarcornSprite()
        .padding(24)
        .background(Color(nsColor: .windowBackgroundColor))
}

#Preview("Starcorn Bundle Resource") {
    VStack(alignment: .leading, spacing: 8) {
        if let petURL = OpenPetsPreviewResources.starcornResourceURL(named: "pet", extension: "json") {
            Text(petURL.lastPathComponent)
        }
        PreviewStarcornSprite()
    }
}

private final class OpenPetsPreviewBundleToken {}

private enum OpenPetsPreviewResources {
    static func starcornResourceURL(named name: String, extension pathExtension: String) -> URL? {
        let subdirectory = "Pets/starcorn"
        let filename = "\(name).\(pathExtension)"
        let packageResourceURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("Resources")
            .appendingPathComponent(subdirectory)
            .appendingPathComponent(filename)

        let bundles = [
            Bundle(for: OpenPetsPreviewBundleToken.self),
            Bundle.main
        ]
        for bundle in bundles {
            if let url = bundle.url(forResource: name, withExtension: pathExtension, subdirectory: subdirectory) {
                return url
            }
            if let url = bundle.url(
                forResource: name,
                withExtension: pathExtension,
                subdirectory: "OpenPets_OpenPetsCore.bundle/\(subdirectory)"
            ) {
                return url
            }
            if let url = bundle.resourceURL?
                .appendingPathComponent(subdirectory)
                .appendingPathComponent(filename),
                FileManager.default.fileExists(atPath: url.path) {
                return url
            }
        }

        guard FileManager.default.fileExists(atPath: packageResourceURL.path) else {
            return nil
        }
        return packageResourceURL
    }
}

#Preview("Messaging Blocks") {
    OpenPetsMessagingPreviewGallery()
}
#endif
