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

struct PetLaunchMotion {
    struct Step {
        var origin: CGPoint
        var velocity: CGVector
        var animation: PetAnimation
        var shouldStop: Bool
    }

    static let launchSpeedThreshold: CGFloat = 650
    static let stopSpeedThreshold: CGFloat = 45
    static let minimumHorizontalAnimationSpeed: CGFloat = 15
    static let frameInterval: TimeInterval = 1.0 / 60.0
    private static let decelerationRate: CGFloat = 3.8

    static func shouldLaunch(velocity: CGVector) -> Bool {
        speed(of: velocity) >= launchSpeedThreshold
    }

    static func animation(for velocity: CGVector, fallback: PetAnimation) -> PetAnimation {
        if velocity.dx > minimumHorizontalAnimationSpeed {
            return .runningRight
        }
        if velocity.dx < -minimumHorizontalAnimationSpeed {
            return .runningLeft
        }
        return fallback == .runningLeft ? .runningLeft : .runningRight
    }

    static func step(
        origin: CGPoint,
        velocity: CGVector,
        movingFrame: CGRect,
        visibleFrame: CGRect,
        fallbackAnimation: PetAnimation,
        deltaTime: TimeInterval
    ) -> Step {
        var nextOrigin = CGPoint(
            x: origin.x + velocity.dx * deltaTime,
            y: origin.y + velocity.dy * deltaTime
        )
        var nextVelocity = velocity
        let minimumOrigin = CGPoint(
            x: visibleFrame.minX - movingFrame.minX,
            y: visibleFrame.minY - movingFrame.minY
        )
        let maximumOrigin = CGPoint(
            x: visibleFrame.maxX - movingFrame.maxX,
            y: visibleFrame.maxY - movingFrame.maxY
        )

        if nextOrigin.x < minimumOrigin.x {
            nextOrigin.x = minimumOrigin.x
            if nextVelocity.dx < 0 {
                nextVelocity.dx = 0
            }
        } else if nextOrigin.x > maximumOrigin.x {
            nextOrigin.x = maximumOrigin.x
            if nextVelocity.dx > 0 {
                nextVelocity.dx = 0
            }
        }

        if nextOrigin.y < minimumOrigin.y {
            nextOrigin.y = minimumOrigin.y
            if nextVelocity.dy < 0 {
                nextVelocity.dy = 0
            }
        } else if nextOrigin.y > maximumOrigin.y {
            nextOrigin.y = maximumOrigin.y
            if nextVelocity.dy > 0 {
                nextVelocity.dy = 0
            }
        }

        let decay = exp(-decelerationRate * deltaTime)
        nextVelocity.dx *= decay
        nextVelocity.dy *= decay

        return Step(
            origin: nextOrigin,
            velocity: nextVelocity,
            animation: animation(for: nextVelocity, fallback: fallbackAnimation),
            shouldStop: speed(of: nextVelocity) < stopSpeedThreshold
        )
    }

    private static func speed(of velocity: CGVector) -> CGFloat {
        hypot(velocity.dx, velocity.dy)
    }
}

public enum OpenPetsHost {
    @MainActor
    public static func run(configuration: OpenPetsHostConfiguration) throws {
        let session = OpenPetsHostSession(
            configuration: configuration,
            terminatesApplicationOnShutdown: true
        )
        try session.start()
        let app = NSApplication.shared
        let delegate = OpenPetsApplicationDelegate(session: session)
        OpenPetsRuntime.current = OpenPetsRuntime(delegate: delegate)
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}

@MainActor
public final class OpenPetsHostSession {
    public private(set) var configuration: OpenPetsHostConfiguration
    public let terminatesApplicationOnShutdown: Bool
    private var controller: PetHostController?
    private var server: OpenPetsServer?

    public var isRunning: Bool {
        controller != nil
    }

    public var petManifest: PetManifest? {
        controller?.petManifest
    }

    public init(
        configuration: OpenPetsHostConfiguration,
        terminatesApplicationOnShutdown: Bool = false
    ) {
        self.configuration = configuration
        self.terminatesApplicationOnShutdown = terminatesApplicationOnShutdown
    }

    public func start() throws {
        guard !isRunning else { return }

        let petBundle = try PetBundle.load(from: configuration.petDirectoryURL)
        let controller = try PetHostController(
            petBundle: petBundle,
            display: configuration.display,
            positionStore: PetPositionStore(url: configuration.positionStoreURL)
        )
        let bridge = PetHostCommandBridge(session: self)
        let server = OpenPetsServer(socketPath: configuration.socketPath) { command in
            bridge.handle(command)
        }

        do {
            try server.start()
        } catch {
            controller.close()
            throw error
        }

        self.controller = controller
        self.server = server
        controller.show()
    }

    public func stop() {
        server?.stop()
        server = nil
        controller?.savePosition()
        controller?.close()
        controller = nil
    }

    @discardableResult
    public func handle(_ command: PetCommand) -> PetResponse {
        switch command {
        case .ping:
            return PetResponse(ok: isRunning, message: isRunning ? "pong" : "pet is not running")
        case .shutdown:
            stop()
            if terminatesApplicationOnShutdown {
                NSApplication.shared.terminate(nil)
            }
            return PetResponse(ok: true, message: "shutting down")
        case .notify(let notification):
            guard let controller else {
                return PetResponse(ok: false, message: "pet is not running")
            }
            let resolvedNotification = notification.resolvingThreadId()
            controller.apply(.notify(resolvedNotification))
            return PetResponse(ok: true, threadId: resolvedNotification.threadId)
        default:
            guard let controller else {
                return PetResponse(ok: false, message: "pet is not running")
            }
            controller.apply(command)
            return PetResponse(ok: true)
        }
    }

    public func apply(_ command: PetCommand) -> PetResponse {
        handle(command)
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
    let session: OpenPetsHostSession

    init(session: OpenPetsHostSession) {
        self.session = session
    }

    func applicationWillTerminate(_ notification: Notification) {
        session.stop()
    }
}

private final class PetHostCommandBridge: @unchecked Sendable {
    @MainActor private let session: OpenPetsHostSession

    @MainActor
    init(session: OpenPetsHostSession) {
        self.session = session
    }

    func handle(_ command: PetCommand) -> PetResponse {
        let response: PetResponse
        switch command {
        case .ping:
            response = PetResponse(ok: true, message: "pong")
        case .shutdown:
            response = PetResponse(ok: true, message: "shutting down")
        case .notify(let notification):
            let resolvedNotification = notification.resolvingThreadId()
            response = PetResponse(ok: true, threadId: resolvedNotification.threadId)
            DispatchQueue.main.async { [self] in
                Task { @MainActor in
                    session.handle(.notify(resolvedNotification))
                }
            }
            return response
        default:
            response = PetResponse(ok: true)
        }

        DispatchQueue.main.async { [self] in
            Task { @MainActor in
                session.handle(command)
            }
        }
        return response
    }
}

@MainActor
struct PetWindowPositioning {
    static func windowOrigin(preservingPetAnchor petAnchor: CGPoint, petFrame: CGRect) -> CGPoint {
        CGPoint(
            x: petAnchor.x - petFrame.minX,
            y: petAnchor.y - petFrame.minY
        )
    }

    static func legacyContentSize(spriteSize: CGSize, messageAreaHeight: CGFloat) -> CGSize {
        CGSize(
            width: max(316, spriteSize.width + 120),
            height: spriteSize.height + messageAreaHeight
        )
    }

    static func initialPetAnchor(
        storedPosition: StoredPetPosition?,
        legacyContentSize: CGSize,
        spriteSize: CGSize,
        stableSpriteBounds: CGRect,
        defaultWindowOrigin: CGPoint? = nil
    ) -> CGPoint {
        if let storedPosition {
            switch storedPosition.kind {
            case .petAnchor:
                return storedPosition.point
            case .windowOrigin:
                let legacySpriteFrame = legacySpriteFrame(
                    contentSize: legacyContentSize,
                    spriteSize: spriteSize
                )
                return CGPoint(
                    x: storedPosition.point.x + legacySpriteFrame.minX + stableSpriteBounds.minX,
                    y: storedPosition.point.y + legacySpriteFrame.minY + stableSpriteBounds.minY
                )
            }
        }

        let defaultOrigin = defaultWindowOrigin ?? self.defaultWindowOrigin(contentSize: legacyContentSize)
        let legacySpriteFrame = legacySpriteFrame(contentSize: legacyContentSize, spriteSize: spriteSize)
        return CGPoint(
            x: defaultOrigin.x + legacySpriteFrame.minX + stableSpriteBounds.minX,
            y: defaultOrigin.y + legacySpriteFrame.minY + stableSpriteBounds.minY
        )
    }

    static func legacySpriteFrame(contentSize: CGSize, spriteSize: CGSize) -> CGRect {
        CGRect(
            x: contentSize.width - spriteSize.width - OpenPetsMessageLayout.sideInset,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
    }

    static func defaultWindowOrigin(contentSize: CGSize) -> CGPoint {
        let screenFrame = NSScreen.main?.visibleFrame ?? CGRect(x: 0, y: 0, width: 1280, height: 800)
        return CGPoint(
            x: screenFrame.maxX - contentSize.width - 40,
            y: screenFrame.minY + 40
        )
    }
}

@MainActor
private final class PetHostController {
    private let petBundle: PetBundle
    private let positionStore: PetPositionStore
    private let window: NSPanel
    private let petView: PetHostView
    private let messagePanel: NSPanel
    private let messageView: PetMessagePanelView
    private let messageAreaHeight: CGFloat
    private var animationTimer: Timer?
    private var glideTimer: Timer?
    private var glideVelocity = CGVector.zero
    private var lastGlideUpdateTime: TimeInterval?
    private var glideAnimationFallback: PetAnimation = .runningRight
    private var ttlWorkItem: DispatchWorkItem?
    private var messageWorkItems: [String: DispatchWorkItem] = [:]
    private var currentAnimation: PetAnimation = .idle
    private var currentFrameIndex = 0
    private var remainingAnimationCycles: Int?

    var petManifest: PetManifest {
        petBundle.manifest
    }

    init(petBundle: PetBundle, display: OpenPetsDisplayConfiguration, positionStore: PetPositionStore) throws {
        self.petBundle = petBundle
        self.positionStore = positionStore
        messageAreaHeight = max(display.messageAreaHeight, 108)

        let frames = try PetHostController.loadFrames(from: petBundle)
        let spriteSize = CGSize(
            width: CGFloat(petBundle.atlas.cellWidth) * display.scale,
            height: CGFloat(petBundle.atlas.cellHeight) * display.scale
        )
        let legacyContentSize = PetWindowPositioning.legacyContentSize(
            spriteSize: spriteSize,
            messageAreaHeight: messageAreaHeight
        )
        let stableSpriteBounds = PetSpriteVisibility.stableVisibleBounds(
            in: frames,
            spriteSize: spriteSize
        )
        petView = PetHostView(
            spriteSize: spriteSize,
            stableSpriteBounds: stableSpriteBounds,
            frames: frames
        )
        messageView = PetMessagePanelView(
            petSize: stableSpriteBounds.size,
            messageAreaHeight: messageAreaHeight
        )

        let initialAnchor = PetWindowPositioning.initialPetAnchor(
            storedPosition: positionStore.loadStoredPosition(forPetID: petBundle.manifest.id),
            legacyContentSize: legacyContentSize,
            spriteSize: spriteSize,
            stableSpriteBounds: stableSpriteBounds
        )
        let contentSize = petView.bounds.size
        let initialOrigin = PetWindowPositioning.windowOrigin(
            preservingPetAnchor: initialAnchor,
            petFrame: petView.petAnchorFrame
        )
        window = NSPanel(
            contentRect: CGRect(origin: initialOrigin, size: contentSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        messagePanel = NSPanel(
            contentRect: CGRect(origin: .zero, size: .zero),
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

        messagePanel.backgroundColor = .clear
        messagePanel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        messagePanel.contentView = messageView
        messagePanel.hasShadow = false
        messagePanel.ignoresMouseEvents = false
        messagePanel.isMovableByWindowBackground = false
        messagePanel.isOpaque = false
        messagePanel.level = .statusBar

        petView.onClick = { [weak self] in
            self?.play(.waving, loop: false, ttlSeconds: nil)
        }
        petView.onDragStart = { [weak self] in
            self?.cancelLaunchGlide()
            self?.messagePanel.ignoresMouseEvents = true
        }
        petView.onDragMove = { [weak self] _ in
            self?.positionMessagePanel()
        }
        petView.onDragDirectionChange = { [weak self] direction in
            self?.switchDragDirection(to: direction)
        }
        petView.onDragEnd = { [weak self] velocity, fallbackAnimation in
            self?.messagePanel.ignoresMouseEvents = false
            self?.handleDragEnd(releaseVelocity: velocity, fallbackAnimation: fallbackAnimation)
        }
        petView.onInteractionEnd = { [weak self] in
            self?.messagePanel.ignoresMouseEvents = false
        }
        messageView.onDismissMessage = { [weak self] threadId in
            self?.clearBubble(threadId: threadId)
        }
        messageView.onLayoutChanged = { [weak self] in
            self?.positionMessagePanel()
        }

        play(.idle, loop: true, ttlSeconds: nil)
    }

    func show() {
        window.orderFrontRegardless()
        if messageView.hasVisibleMessages {
            positionMessagePanel()
            messagePanel.orderFrontRegardless()
        }
    }

    func close() {
        animationTimer?.invalidate()
        animationTimer = nil
        cancelLaunchGlide()
        ttlWorkItem?.cancel()
        ttlWorkItem = nil
        cancelMessageWorkItems()
        messagePanel.orderOut(nil)
        messagePanel.close()
        window.orderOut(nil)
        window.close()
    }

    func apply(_ command: PetCommand) {
        switch command {
        case .notify(let notification):
            let resolvedNotification = notification.resolvingThreadId()
            setBubble(
                bubble(for: resolvedNotification),
                threadId: resolvedNotification.threadId ?? UUID().uuidString,
                ttlSeconds: resolvedNotification.ttlSeconds
            )
            if let finiteAnimation = finiteAnimation(forStatusKind: notification.status) {
                play(finiteAnimation, loopCount: 3, ttlSeconds: nil)
            } else {
                play(animation(forStatusKind: notification.status), loop: true, ttlSeconds: notification.ttlSeconds)
            }
        case .playAnimation(let name, let loop, let ttlSeconds):
            play(name, loop: loop ?? true, ttlSeconds: ttlSeconds)
        case .clearMessage(let threadId):
            clearBubble(threadId: threadId)
        case .ping, .shutdown:
            break
        }
    }

    func savePosition() {
        try? positionStore.savePosition(petAnchorInScreen(), kind: .petAnchor, forPetID: petBundle.manifest.id)
    }

    private func petAnchorInScreen() -> CGPoint {
        CGPoint(
            x: window.frame.origin.x + petView.petAnchorFrame.minX,
            y: window.frame.origin.y + petView.petAnchorFrame.minY
        )
    }

    private func setBubble(_ bubble: PetBubble, threadId: String, ttlSeconds: Double?) {
        messageWorkItems[threadId]?.cancel()
        messageView.setBubble(bubble, threadId: threadId)
        positionMessagePanel()
        messagePanel.orderFrontRegardless()

        guard let ttlSeconds, ttlSeconds > 0 else { return }
        let workItem = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                self?.clearBubble(threadId: threadId)
            }
        }
        messageWorkItems[threadId] = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + ttlSeconds, execute: workItem)
    }

    private func clearBubble(threadId: String) {
        messageWorkItems[threadId]?.cancel()
        messageWorkItems[threadId] = nil
        messageView.clearBubble(threadId: threadId)
        if messageView.hasVisibleMessages {
            positionMessagePanel()
        } else {
            messagePanel.orderOut(nil)
        }
    }

    private func cancelMessageWorkItems() {
        for workItem in messageWorkItems.values {
            workItem.cancel()
        }
        messageWorkItems.removeAll()
    }

    private func play(_ animation: PetAnimation, loop: Bool, ttlSeconds: Double?) {
        play(animation, loopCount: loop ? nil : 1, ttlSeconds: ttlSeconds)
    }

    private func play(_ animation: PetAnimation, loopCount: Int?, ttlSeconds: Double?) {
        cancelLaunchGlide()
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

    private func handleDragEnd(releaseVelocity: CGVector, fallbackAnimation: PetAnimation?) {
        let fallbackAnimation = directionalAnimation(from: fallbackAnimation)
        guard PetLaunchMotion.shouldLaunch(velocity: releaseVelocity) else {
            savePosition()
            play(.idle, loop: true, ttlSeconds: nil)
            return
        }

        glideVelocity = releaseVelocity
        glideAnimationFallback = PetLaunchMotion.animation(for: releaseVelocity, fallback: fallbackAnimation)
        switchDragDirection(to: glideAnimationFallback)
        lastGlideUpdateTime = ProcessInfo.processInfo.systemUptime
        glideTimer?.invalidate()
        glideTimer = Timer.scheduledTimer(
            withTimeInterval: PetLaunchMotion.frameInterval,
            repeats: true
        ) { [weak self] _ in
            Task { @MainActor in
                self?.advanceLaunchGlide()
            }
        }
    }

    private func advanceLaunchGlide() {
        let now = ProcessInfo.processInfo.systemUptime
        let previousTime = lastGlideUpdateTime ?? now
        lastGlideUpdateTime = now
        let deltaTime = min(max(now - previousTime, 1.0 / 120.0), 1.0 / 30.0)
        let step = PetLaunchMotion.step(
            origin: window.frame.origin,
            velocity: glideVelocity,
            movingFrame: petView.visibleSpriteFrame,
            visibleFrame: visibleFrameForGlide(),
            fallbackAnimation: glideAnimationFallback,
            deltaTime: deltaTime
        )

        window.setFrameOrigin(step.origin)
        positionMessagePanel()
        glideVelocity = step.velocity
        if step.animation != glideAnimationFallback {
            glideAnimationFallback = step.animation
            switchDragDirection(to: step.animation)
        }

        if step.shouldStop {
            finishLaunchGlide()
        }
    }

    private func finishLaunchGlide() {
        cancelLaunchGlide()
        savePosition()
        play(.idle, loop: true, ttlSeconds: nil)
    }

    private func cancelLaunchGlide() {
        glideTimer?.invalidate()
        glideTimer = nil
        lastGlideUpdateTime = nil
    }

    private func visibleFrameForGlide() -> CGRect {
        if let screen = window.screen ?? NSScreen.main ?? NSScreen.screens.first {
            return screen.visibleFrame
        }
        return window.frame
    }

    private func positionMessagePanel() {
        guard messageView.hasVisibleMessages else { return }
        let petAnchor = petAnchorInScreen()
        messageView.resizeWindow(preservingPetAnchor: petAnchor)
    }

    private func directionalAnimation(from animation: PetAnimation?) -> PetAnimation {
        if let animation {
            return animation == .runningLeft ? .runningLeft : .runningRight
        }
        if currentAnimation == .runningLeft {
            return .runningLeft
        }
        return .runningRight
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

    private func bubble(for notification: PetNotification) -> PetBubble {
        PetBubble(
            title: notification.title,
            detail: notification.text,
            indicator: indicator(forStatusKind: notification.status),
            action: action(for: notification)
        )
    }

    private func indicator(forStatusKind kind: String) -> PetBubbleIndicator {
        openPetsBubbleIndicator(forStatusKind: kind)
    }

    private func action(for notification: PetNotification) -> PetBubbleAction? {
        guard
            let actionURL = notification.url?.trimmingCharacters(in: .whitespacesAndNewlines),
            !actionURL.isEmpty,
            let url = URL(string: actionURL)
        else {
            return nil
        }

        let label = notification.buttonLabel?.trimmingCharacters(in: .whitespacesAndNewlines)
        return PetBubbleAction(
            label: label?.isEmpty == false ? label! : "open",
            url: url
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

struct PetMessage: Equatable, Identifiable {
    var id: String { threadId }
    var threadId: String
    var bubble: PetBubble
}

struct PetMessageStack: Equatable {
    static let visibleLimit = 4

    private var orderedThreadIds: [String] = []
    private var bubblesByThreadId: [String: PetBubble] = [:]

    var activeMessages: [PetMessage] {
        orderedThreadIds.compactMap { threadId in
            bubblesByThreadId[threadId].map { PetMessage(threadId: threadId, bubble: $0) }
        }
    }

    var activeCount: Int {
        activeMessages.count
    }

    mutating func setBubble(_ bubble: PetBubble, threadId: String) {
        if bubblesByThreadId[threadId] == nil {
            orderedThreadIds.append(threadId)
        }
        bubblesByThreadId[threadId] = bubble
    }

    mutating func clearBubble(threadId: String) {
        bubblesByThreadId[threadId] = nil
        orderedThreadIds.removeAll { $0 == threadId }
    }

    func visibleMessages(limit: Int = visibleLimit) -> [PetMessage] {
        Array(activeMessages.suffix(max(0, limit)))
    }

    func hiddenMessageCount(limit: Int = visibleLimit) -> Int {
        max(0, activeCount - max(0, limit))
    }
}

struct PetBubble: Equatable {
    var title: String
    var detail: String?
    var indicator: PetBubbleIndicator
    var action: PetBubbleAction? = nil
}

struct PetBubbleAction: Equatable {
    var label: String
    var url: URL
}

enum PetBubbleIndicator: Equatable {
    case none
    case working
    case waiting
    case review
    case success
    case attention
}

func openPetsBubbleIndicator(forStatusKind kind: String) -> PetBubbleIndicator {
    switch kind.lowercased() {
    case "waiting":
        .waiting
    case "review", "reviewing":
        .review
    case "done", "success", "completed", "complete", "committed":
        .success
    case "failed", "fail", "failure", "error":
        .attention
    case "attention", "reply", "message":
        .none
    default:
        .working
    }
}

@MainActor
final class PetHostView: NSView {
    var onClick: (() -> Void)? {
        get { spriteView.onClick }
        set { spriteView.onClick = newValue }
    }

    var onDragMove: ((CGPoint) -> Void)? {
        get { spriteView.onDragMove }
        set { spriteView.onDragMove = newValue }
    }

    var onDragDirectionChange: ((PetAnimation) -> Void)? {
        get { spriteView.onDragDirectionChange }
        set { spriteView.onDragDirectionChange = newValue }
    }

    var onDragStart: (() -> Void)? {
        get { spriteView.onDragStart }
        set { spriteView.onDragStart = newValue }
    }

    var onDragEnd: ((CGVector, PetAnimation?) -> Void)? {
        get { spriteView.onDragEnd }
        set { spriteView.onDragEnd = newValue }
    }

    var onInteractionEnd: (() -> Void)? {
        get { spriteView.onInteractionEnd }
        set { spriteView.onInteractionEnd = newValue }
    }

    var visibleSpriteFrame: CGRect {
        bounds
    }

    var petAnchorFrame: CGRect {
        bounds
    }

    private let spriteView: PetSpriteView
    private let spriteSize: CGSize
    private let stableSpriteBounds: CGRect

    init(
        spriteSize: CGSize,
        stableSpriteBounds: CGRect,
        frames: [PetAnimation: [CGImage]]
    ) {
        self.spriteSize = spriteSize
        self.stableSpriteBounds = stableSpriteBounds
        let size = CGSize(
            width: max(1, stableSpriteBounds.width),
            height: max(1, stableSpriteBounds.height)
        )
        spriteView = PetSpriteView(
            frame: CGRect(origin: .zero, size: size),
            spriteSize: spriteSize,
            frames: frames
        )
        super.init(frame: CGRect(origin: .zero, size: size))
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
        addSubview(spriteView)
        applyCurrentLayoutToSubviews()
    }

    required init?(coder: NSCoder) {
        nil
    }

    override var isOpaque: Bool {
        false
    }

    override func hitTest(_ point: NSPoint) -> NSView? {
        let hitView = super.hitTest(point)
        return hitView === self ? nil : hitView
    }

    override func layout() {
        super.layout()
        applyCurrentLayoutToSubviews()
    }

    func set(animation: PetAnimation, frameIndex: Int) {
        spriteView.set(animation: animation, frameIndex: frameIndex)
    }

    private func applyCurrentLayoutToSubviews() {
        spriteView.frame = bounds
        spriteView.spriteFrame = CGRect(
            x: -stableSpriteBounds.minX,
            y: -stableSpriteBounds.minY,
            width: spriteSize.width,
            height: spriteSize.height
        )
    }
}

@MainActor
final class PetMessagePanelView: NSView {
    var onDismissMessage: ((String) -> Void)?
    var onLayoutChanged: (() -> Void)?

    var hasVisibleMessages: Bool {
        !messageStack.visibleMessages().isEmpty
    }

    var petAnchorFrame: CGRect {
        currentMessageLayout.petFrame
    }

    private lazy var bubbleView: MessageHostingView = {
        MessageHostingView(rootView: OpenPetsMessageView(
            messages: [],
            hiddenMessageCount: 0,
            isCollapsed: false,
            activeMessageCount: 0,
            layout: .empty,
            cardFrames: [],
            onDismiss: { _ in },
            onToggle: {}
        ))
    }()
    private let petSize: CGSize
    private let messageAreaHeight: CGFloat
    private var messageStack = PetMessageStack()
    private var isMessageStackCollapsed = false
    private var currentMessageLayout = OpenPetsMessageLayout.empty
    private var mouseDownMessageTarget: MessageMouseTarget?

    private enum MessageMouseTarget: Equatable {
        case toggle
        case dismiss(String)
    }

    init(petSize: CGSize, messageAreaHeight: CGFloat) {
        self.petSize = petSize
        self.messageAreaHeight = messageAreaHeight
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor.clear.cgColor
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
        if messageMouseTarget(at: point) != nil {
            return self
        }
        let hitView = super.hitTest(point)
        return hitView === self ? nil : hitView
    }

    override func mouseDown(with event: NSEvent) {
        mouseDownMessageTarget = messageMouseTarget(at: convert(event.locationInWindow, from: nil))
    }

    override func mouseUp(with event: NSEvent) {
        let target = messageMouseTarget(at: convert(event.locationInWindow, from: nil))
        defer { mouseDownMessageTarget = nil }

        guard let mouseDownMessageTarget, mouseDownMessageTarget == target else {
            return
        }

        switch mouseDownMessageTarget {
        case .toggle:
            toggleMessageStackCollapsed()
        case .dismiss(let threadId):
            onDismissMessage?(threadId)
        }
    }

    override func layout() {
        super.layout()
        bubbleView.frame = bounds
    }

    func setBubble(_ bubble: PetBubble, threadId: String) {
        messageStack.setBubble(bubble, threadId: threadId)
        relayoutMessages()
    }

    func clearBubble(threadId: String) {
        messageStack.clearBubble(threadId: threadId)
        if messageStack.activeCount == 0 {
            isMessageStackCollapsed = false
        }
        relayoutMessages()
    }

    func resizeWindow(preservingPetAnchor petAnchor: CGPoint) {
        guard let window, hasVisibleMessages else { return }
        var frame = window.frame
        frame.origin = PetWindowPositioning.windowOrigin(
            preservingPetAnchor: petAnchor,
            petFrame: currentMessageLayout.petFrame
        )
        frame.size = currentMessageLayout.containerSize
        window.setFrame(frame, display: false)
    }

    private func relayoutMessages() {
        let messages = messageStack.visibleMessages()
        if messages.isEmpty {
            currentMessageLayout = .empty
            bubbleView.frame = .zero
            bubbleView.interactiveRects = []
            bubbleView.dismissRegions = []
            updateMessageView(messages: [], hiddenMessageCount: 0, layout: .empty)
            setFrameSize(.zero)
            onLayoutChanged?()
            return
        }

        currentMessageLayout = OpenPetsMessageLayout.makeMessagePanel(
            messages: messages,
            hiddenMessageCount: messageStack.hiddenMessageCount(),
            isCollapsed: isMessageStackCollapsed,
            petSize: petSize,
            messageAreaHeight: messageAreaHeight
        )
        setFrameSize(currentMessageLayout.containerSize)
        bubbleView.frame = bounds
        updateMessageHitRegions(messages: messages, layout: currentMessageLayout)
        updateMessageView(
            messages: messages,
            hiddenMessageCount: messageStack.hiddenMessageCount(),
            layout: currentMessageLayout
        )
        onLayoutChanged?()
    }

    private func updateMessageView(
        messages: [PetMessage],
        hiddenMessageCount: Int,
        layout: OpenPetsMessageLayout
    ) {
        bubbleView.rootView = OpenPetsMessageView(
            messages: messages,
            hiddenMessageCount: hiddenMessageCount,
            isCollapsed: isMessageStackCollapsed,
            activeMessageCount: messageStack.activeCount,
            layout: layout,
            cardFrames: layout.cardFrames,
            onDismiss: { [weak self] threadId in
                self?.onDismissMessage?(threadId)
            },
            onToggle: { [weak self] in
                self?.toggleMessageStackCollapsed()
            }
        )
        bubbleView.isHidden = messages.isEmpty
    }

    private func updateMessageHitRegions(messages: [PetMessage], layout: OpenPetsMessageLayout) {
        bubbleView.interactiveRects = layout.cardFrames + (layout.toggleFrame.isEmpty ? [] : [layout.toggleFrame])
        bubbleView.dismissRegions = zip(messages, layout.cardFrames).map { message, cardFrame in
            MessageHostingView.InteractiveRegion(
                threadId: message.threadId,
                cardFrame: cardFrame,
                closeButtonFrame: OpenPetsMessageLayout.closeButtonFrame(in: cardFrame)
            )
        }
        bubbleView.onDismissMessage = { [weak self] threadId in
            self?.onDismissMessage?(threadId)
        }
    }

    private func messageMouseTarget(at point: NSPoint) -> MessageMouseTarget? {
        let normalizedPoint = isFlipped
            ? CGPoint(x: point.x, y: bounds.height - point.y)
            : point
        if currentMessageLayout.toggleFrame.contains(normalizedPoint), messageStack.activeCount > 0 {
            return .toggle
        }
        if let dismissRegion = bubbleView.dismissRegions.first(where: { $0.closeButtonFrame.contains(normalizedPoint) }) {
            return .dismiss(dismissRegion.threadId)
        }
        return nil
    }

    private func toggleMessageStackCollapsed() {
        guard messageStack.activeCount > 0 else { return }
        isMessageStackCollapsed.toggle()
        relayoutMessages()
    }
}

struct OpenPetsMessageLayout {
    static let toggleDiameter: CGFloat = 36
    static let verticalGap: CGFloat = 10
    static let stackGap: CGFloat = 6
    static let toggleGapBelowCard: CGFloat = 4
    static let sideInset: CGFloat = 12
    static let maxCardWidth: CGFloat = 260
    static let closeButtonSize = CGSize(width: 22, height: 22)
    static let closeButtonInset: CGFloat = 8
    static let empty = OpenPetsMessageLayout(
        containerSize: .zero,
        cardFrames: [],
        spriteFrame: .zero,
        petFrame: .zero,
        toggleFrame: .zero
    )

    var containerSize: CGSize
    var cardFrames: [CGRect]
    var spriteFrame: CGRect
    var petFrame: CGRect
    var toggleFrame: CGRect

    var cardFrame: CGRect {
        cardFrames.first ?? .zero
    }

    static func closeButtonFrame(in cardFrame: CGRect) -> CGRect {
        CGRect(
            x: cardFrame.minX + closeButtonInset,
            y: cardFrame.maxY - closeButtonInset - closeButtonSize.height,
            width: closeButtonSize.width,
            height: closeButtonSize.height
        )
    }

    @MainActor
    static func make(
        bubble: PetBubble,
        isCollapsed: Bool,
        containerWidth: CGFloat,
        spriteSize: CGSize,
        messageAreaHeight: CGFloat
    ) -> OpenPetsMessageLayout {
        _ = isCollapsed
        return make(
            messages: [PetMessage(threadId: "preview", bubble: bubble)],
            hiddenMessageCount: 0,
            isCollapsed: isCollapsed,
            containerWidth: containerWidth,
            spriteSize: spriteSize,
            messageAreaHeight: messageAreaHeight
        )
    }

    @MainActor
    static func make(
        messages: [PetMessage],
        hiddenMessageCount: Int,
        isCollapsed: Bool = false,
        containerWidth: CGFloat,
        spriteSize: CGSize,
        messageAreaHeight: CGFloat
    ) -> OpenPetsMessageLayout {
        let cardMaxWidth = min(maxCardWidth, max(1, containerWidth - sideInset * 2))
        let rightEdge = containerWidth - sideInset
        let spriteFrame = CGRect(
            x: rightEdge - spriteSize.width,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
        var cardFrames: [CGRect] = []
        var nextY = spriteFrame.maxY + verticalGap

        for message in isCollapsed ? [] : messages {
            let cardSize = OpenPetsBubbleContentView.size(
                for: message.bubble,
                maxWidth: cardMaxWidth,
                messageAreaHeight: messageAreaHeight
            )
            cardFrames.append(CGRect(
                x: rightEdge - cardSize.width,
                y: nextY,
                width: cardSize.width,
                height: cardSize.height
            ))
            nextY += cardSize.height + stackGap
        }

        let toggleFrame: CGRect
        if messages.isEmpty {
            toggleFrame = .zero
        } else {
            let bottomCardMinY = cardFrames.first?.minY ?? spriteFrame.maxY + verticalGap
            toggleFrame = CGRect(
                x: rightEdge - toggleDiameter,
                y: bottomCardMinY - toggleDiameter - toggleGapBelowCard,
                width: toggleDiameter,
                height: toggleDiameter
            )
        }

        if !cardFrames.isEmpty {
            nextY -= stackGap
        }

        let contentHeight: CGFloat
        if messages.isEmpty {
            contentHeight = spriteSize.height
        } else if isCollapsed {
            contentHeight = max(spriteSize.height + toggleDiameter / 2, toggleFrame.maxY)
        } else {
            contentHeight = max(spriteSize.height, nextY)
        }

        return OpenPetsMessageLayout(
            containerSize: CGSize(width: containerWidth, height: contentHeight),
            cardFrames: cardFrames,
            spriteFrame: spriteFrame,
            petFrame: spriteFrame,
            toggleFrame: toggleFrame
        )
    }

    @MainActor
    static func makeMinimal(
        messages: [PetMessage],
        hiddenMessageCount: Int,
        isCollapsed: Bool = false,
        spriteSize: CGSize,
        stableSpriteBounds: CGRect,
        messageAreaHeight: CGFloat
    ) -> OpenPetsMessageLayout {
        _ = hiddenMessageCount
        let petSize = CGSize(
            width: max(1, stableSpriteBounds.width),
            height: max(1, stableSpriteBounds.height)
        )
        let cardSizes = isCollapsed ? [] : messages.map {
            OpenPetsBubbleContentView.size(
                for: $0.bubble,
                maxWidth: maxCardWidth,
                messageAreaHeight: messageAreaHeight
            )
        }
        let widestCard = cardSizes.map(\.width).max() ?? 0
        let rightEdge = max(petSize.width, widestCard, messages.isEmpty ? 0 : toggleDiameter)
        let petFrame = CGRect(
            x: rightEdge - petSize.width,
            y: 0,
            width: petSize.width,
            height: petSize.height
        )
        let spriteFrame = CGRect(
            x: petFrame.minX - stableSpriteBounds.minX,
            y: petFrame.minY - stableSpriteBounds.minY,
            width: spriteSize.width,
            height: spriteSize.height
        )

        var cardFrames: [CGRect] = []
        var nextY = petFrame.maxY + verticalGap
        for cardSize in cardSizes {
            cardFrames.append(CGRect(
                x: rightEdge - cardSize.width,
                y: nextY,
                width: cardSize.width,
                height: cardSize.height
            ))
            nextY += cardSize.height + stackGap
        }

        let toggleFrame: CGRect
        if messages.isEmpty {
            toggleFrame = .zero
        } else {
            let bottomCardMinY = cardFrames.first?.minY ?? petFrame.maxY + verticalGap
            toggleFrame = CGRect(
                x: rightEdge - toggleDiameter,
                y: bottomCardMinY - toggleDiameter - toggleGapBelowCard,
                width: toggleDiameter,
                height: toggleDiameter
            )
        }

        let occupiedFrames = [petFrame] + cardFrames + (toggleFrame.isEmpty ? [] : [toggleFrame])
        let contentBounds = occupiedFrames.reduce(CGRect.null) { partialResult, frame in
            partialResult.union(frame)
        }
        let offset = CGVector(dx: -contentBounds.minX, dy: -contentBounds.minY)
        let normalizedCardFrames = cardFrames.map { $0.offsetBy(dx: offset.dx, dy: offset.dy) }

        return OpenPetsMessageLayout(
            containerSize: contentBounds.size,
            cardFrames: normalizedCardFrames,
            spriteFrame: spriteFrame.offsetBy(dx: offset.dx, dy: offset.dy),
            petFrame: petFrame.offsetBy(dx: offset.dx, dy: offset.dy),
            toggleFrame: toggleFrame.isEmpty ? .zero : toggleFrame.offsetBy(dx: offset.dx, dy: offset.dy)
        )
    }

    @MainActor
    static func makeMessagePanel(
        messages: [PetMessage],
        hiddenMessageCount: Int,
        isCollapsed: Bool = false,
        petSize: CGSize,
        messageAreaHeight: CGFloat
    ) -> OpenPetsMessageLayout {
        _ = hiddenMessageCount
        guard !messages.isEmpty else { return .empty }

        let cardSizes = isCollapsed ? [] : messages.map {
            OpenPetsBubbleContentView.size(
                for: $0.bubble,
                maxWidth: maxCardWidth,
                messageAreaHeight: messageAreaHeight
            )
        }
        let widestCard = cardSizes.map(\.width).max() ?? 0
        let rightEdge = max(petSize.width, widestCard, toggleDiameter)
        let petFrame = CGRect(
            x: rightEdge - petSize.width,
            y: 0,
            width: max(1, petSize.width),
            height: max(1, petSize.height)
        )

        var cardFrames: [CGRect] = []
        var nextY = petFrame.maxY + verticalGap
        for cardSize in cardSizes {
            cardFrames.append(CGRect(
                x: rightEdge - cardSize.width,
                y: nextY,
                width: cardSize.width,
                height: cardSize.height
            ))
            nextY += cardSize.height + stackGap
        }

        let toggleFrame = CGRect(
            x: rightEdge - toggleDiameter,
            y: petFrame.maxY + verticalGap,
            width: toggleDiameter,
            height: toggleDiameter
        )
        for index in cardFrames.indices {
            cardFrames[index].origin.y += toggleDiameter + toggleGapBelowCard
        }
        let messageFrames = cardFrames + [toggleFrame]
        let messageBounds = messageFrames.reduce(CGRect.null) { partialResult, frame in
            partialResult.union(frame)
        }
        let offset = CGVector(dx: -messageBounds.minX, dy: -messageBounds.minY)
        let normalizedCardFrames = cardFrames.map { $0.offsetBy(dx: offset.dx, dy: offset.dy) }

        return OpenPetsMessageLayout(
            containerSize: messageBounds.size,
            cardFrames: normalizedCardFrames,
            spriteFrame: .zero,
            petFrame: petFrame.offsetBy(dx: offset.dx, dy: offset.dy),
            toggleFrame: toggleFrame.offsetBy(dx: offset.dx, dy: offset.dy)
        )
    }

}

private final class MessageHostingView: NSHostingView<OpenPetsMessageView> {
    struct InteractiveRegion {
        var threadId: String
        var cardFrame: CGRect
        var closeButtonFrame: CGRect
    }

    var interactiveRects: [CGRect] = []
    var dismissRegions: [InteractiveRegion] = []
    var onDismissMessage: ((String) -> Void)?
    private var mouseDownDismissThreadId: String?

    override func hitTest(_ point: NSPoint) -> NSView? {
        guard containsInteractiveContent(point) else { return nil }
        return super.hitTest(point) ?? self
    }

    override func mouseDown(with event: NSEvent) {
        mouseDownDismissThreadId = dismissThreadId(for: event)
        super.mouseDown(with: event)
    }

    override func mouseUp(with event: NSEvent) {
        defer { mouseDownDismissThreadId = nil }
        guard
            let mouseDownDismissThreadId,
            dismissThreadId(for: event) == mouseDownDismissThreadId
        else {
            super.mouseUp(with: event)
            return
        }

        onDismissMessage?(mouseDownDismissThreadId)
    }

    private func containsInteractiveContent(_ point: NSPoint) -> Bool {
        let normalizedPoint = layoutPoint(fromViewPoint: point)
        return interactiveRects.contains { $0.contains(normalizedPoint) }
    }

    private func dismissThreadId(for event: NSEvent) -> String? {
        let point = convert(event.locationInWindow, from: nil)
        let layoutPoint = layoutPoint(fromViewPoint: point)
        return dismissRegions.first { $0.closeButtonFrame.contains(layoutPoint) }?.threadId
    }

    private func layoutPoint(fromViewPoint point: NSPoint) -> CGPoint {
        isFlipped
            ? CGPoint(x: point.x, y: bounds.height - point.y)
            : point
    }
}

private struct OpenPetsMessageView: View {
    let messages: [PetMessage]
    let hiddenMessageCount: Int
    let isCollapsed: Bool
    let activeMessageCount: Int
    let layout: OpenPetsMessageLayout
    let cardFrames: [CGRect]
    let onDismiss: (String) -> Void
    let onToggle: () -> Void
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Group {
            if !messages.isEmpty {
                ZStack(alignment: .topLeading) {
                    if !isCollapsed {
                        ForEach(Array(zip(messages, cardFrames)), id: \.0.threadId) { message, frame in
                            OpenPetsDismissibleBubbleView(message: message, onDismiss: onDismiss)
                                .position(swiftUIPosition(for: frame))
                        }
                    }
                    if !layout.toggleFrame.isEmpty {
                        toggleButton
                            .position(swiftUIPosition(for: layout.toggleFrame))
                    }
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

private struct OpenPetsDismissibleBubbleView: View {
    let message: PetMessage
    let onDismiss: (String) -> Void
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovered = false

    var body: some View {
        OpenPetsBubbleContentView(bubble: message.bubble, showsAction: isHovered)
            .overlay(alignment: .topLeading) {
                if isHovered {
                    Button {
                        onDismiss(message.threadId)
                    } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 9, weight: .bold))
                            .foregroundStyle(.secondary)
                            .frame(
                                width: OpenPetsMessageLayout.closeButtonSize.width,
                                height: OpenPetsMessageLayout.closeButtonSize.height
                            )
                    }
                    .buttonStyle(.plain)
                    .background(closeButtonBackground)
                    .clipShape(Circle())
                    .overlay {
                        Circle()
                            .stroke(Color(nsColor: .separatorColor).opacity(colorScheme == .dark ? 0.55 : 0.35), lineWidth: 1)
                    }
                    .shadow(color: .black.opacity(colorScheme == .dark ? 0.20 : 0.07), radius: 2, x: 0, y: 1)
                    .padding(.top, OpenPetsMessageLayout.closeButtonInset)
                    .padding(.leading, OpenPetsMessageLayout.closeButtonInset)
                    .accessibilityLabel("Dismiss message")
                }
            }
            .contentShape(Rectangle())
            .onHover { hovering in
                isHovered = hovering
            }
    }

    private var closeButtonBackground: some View {
        Color(nsColor: colorScheme == .dark ? .black : .white)
            .opacity(colorScheme == .dark ? 0.82 : 0.94)
    }
}

private struct OpenPetsBubbleContentView: View {
    let bubble: PetBubble
    var showsAction = true
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
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
                if bubble.indicator != .none {
                    indicator(for: bubble.indicator)
                        .frame(width: 16, height: 16)
                }
            }
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
        .overlay(alignment: .bottomTrailing) {
            if showsAction, let action = bubble.action {
                actionButton(action)
                    .padding(.trailing, 8)
                    .padding(.bottom, 6)
            }
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

    private func actionButton(_ action: PetBubbleAction) -> some View {
        Button {
            openActionURL(action.url)
        } label: {
            Text(action.label)
                .font(.system(size: 10.5, weight: .semibold))
                .lineLimit(1)
                .truncationMode(.tail)
                .foregroundStyle(.primary)
                .padding(.horizontal, 7)
                .frame(height: 20)
        }
        .buttonStyle(.plain)
        .background(Color(nsColor: colorScheme == .dark ? .black : .white).opacity(colorScheme == .dark ? 0.82 : 0.94))
        .clipShape(Capsule())
        .overlay {
            Capsule()
                .stroke(Color(nsColor: .separatorColor).opacity(colorScheme == .dark ? 0.55 : 0.35), lineWidth: 1)
        }
        .shadow(color: .black.opacity(colorScheme == .dark ? 0.16 : 0.06), radius: 2, x: 0, y: 1)
        .accessibilityLabel(action.label)
    }

    private func openActionURL(_ url: URL) {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        NSWorkspace.shared.open(url, configuration: configuration) { _, error in
            if let error {
                NSLog("OpenPets could not open action URL \(url.absoluteString): \(error.localizedDescription)")
            }
        }
    }

    @ViewBuilder
    private func indicator(for indicator: PetBubbleIndicator) -> some View {
        switch indicator {
        case .none:
            EmptyView()
        case .working:
            ProgressView()
                .scaleEffect(0.5)
                .opacity(0.7)
        case .waiting:
            ZStack {
                Circle()
                    .fill(Color(nsColor: .systemOrange))
                Image(systemName: "clock")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
            }
        case .review:
            ZStack {
                Circle()
                    .fill(Color(nsColor: .systemPurple))
                Image(systemName: "eye")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.white)
            }
        case .success:
            ZStack {
                Circle()
                    .fill(Color(nsColor: .systemGreen))
                Image(systemName: "checkmark")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundStyle(.white)
            }
        case .attention:
            ZStack {
                Circle()
                    .fill(Color(nsColor: .systemRed))
                Image(systemName: "xmark")
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

struct PetSpriteVisibility {
    private let width: Int
    private let height: Int
    private let alphas: [UInt8]

    init?(image: CGImage) {
        let imageWidth = image.width
        let imageHeight = image.height
        guard imageWidth > 0, imageHeight > 0, imageWidth <= Int.max / imageHeight / 4 else { return nil }

        let bytesPerPixel = 4
        let bytesPerRow = imageWidth * bytesPerPixel
        var rgba = [UInt8](repeating: 0, count: bytesPerRow * imageHeight)
        let bitmapInfo = CGImageAlphaInfo.premultipliedLast.rawValue
            | CGBitmapInfo.byteOrder32Big.rawValue

        guard let renderedAlphas = rgba.withUnsafeMutableBytes({ buffer -> [UInt8]? in
            guard
                let context = CGContext(
                    data: buffer.baseAddress,
                    width: imageWidth,
                    height: imageHeight,
                    bitsPerComponent: 8,
                    bytesPerRow: bytesPerRow,
                    space: CGColorSpaceCreateDeviceRGB(),
                    bitmapInfo: bitmapInfo
                )
            else {
                return nil
            }

            context.interpolationQuality = .none
            context.draw(image, in: CGRect(x: 0, y: 0, width: imageWidth, height: imageHeight))

            let bytes = buffer.bindMemory(to: UInt8.self)
            var alphas = [UInt8](repeating: 0, count: imageWidth * imageHeight)
            for index in 0..<alphas.count {
                alphas[index] = bytes[index * bytesPerPixel + 3]
            }
            return alphas
        }) else {
            return nil
        }

        width = imageWidth
        height = imageHeight
        alphas = renderedAlphas
    }

    func visibleBounds(in frame: CGRect) -> CGRect? {
        var minX = width
        var minY = height
        var maxX = -1
        var maxY = -1

        for pixelY in 0..<height {
            for pixelX in 0..<width where alphas[pixelY * width + pixelX] > 0 {
                minX = min(minX, pixelX)
                minY = min(minY, pixelY)
                maxX = max(maxX, pixelX)
                maxY = max(maxY, pixelY)
            }
        }

        guard maxX >= minX, maxY >= minY else { return nil }

        let scaleX = frame.width / CGFloat(width)
        let scaleY = frame.height / CGFloat(height)
        return CGRect(
            x: frame.minX + CGFloat(minX) * scaleX,
            y: frame.minY + CGFloat(minY) * scaleY,
            width: CGFloat(maxX - minX + 1) * scaleX,
            height: CGFloat(maxY - minY + 1) * scaleY
        )
    }

    static func stableVisibleBounds(in frames: [PetAnimation: [CGImage]], spriteSize: CGSize) -> CGRect {
        let fullSpriteBounds = CGRect(origin: .zero, size: spriteSize)
        let images = frames.values.flatMap { $0 }
        var stableBounds = CGRect.null
        for image in images {
            guard let mask = PetSpriteVisibility(image: image) else {
                return fullSpriteBounds
            }
            guard let visibleBounds = mask.visibleBounds(in: fullSpriteBounds) else {
                continue
            }
            stableBounds = stableBounds.union(visibleBounds)
        }

        guard !stableBounds.isNull else {
            return fullSpriteBounds
        }

        return stableBounds
    }
}

final class PetSpriteFrameAsset {
    let image: CGImage
    let renderedImage: NSImage

    init(image: CGImage, spriteSize: CGSize) {
        self.image = image
        renderedImage = NSImage(cgImage: image, size: spriteSize)
    }
}

final class PetSpriteFrameStore {
    private let assetsByAnimation: [PetAnimation: [PetSpriteFrameAsset]]

    init(frames: [PetAnimation: [CGImage]], spriteSize: CGSize) {
        assetsByAnimation = frames.mapValues { images in
            images.map { PetSpriteFrameAsset(image: $0, spriteSize: spriteSize) }
        }
    }

    func asset(for animation: PetAnimation, frameIndex: Int) -> PetSpriteFrameAsset? {
        guard let assets = assetsByAnimation[animation], !assets.isEmpty else { return nil }
        return assets[frameIndex % assets.count]
    }
}

struct PetDragUpdate: Equatable {
    var windowOrigin: CGPoint
    var isDragging: Bool
    var directionChange: PetAnimation?
}

struct PetDragEnd: Equatable {
    var wasDragging: Bool
    var releaseVelocity: CGVector
    var fallbackAnimation: PetAnimation?
}

struct PetDragTracker {
    private var mouseDownScreenLocation = CGPoint.zero
    private var previousDragScreenLocation = CGPoint.zero
    private var mouseDownWindowOrigin = CGPoint.zero
    private var dragging = false
    private var active = false
    private var lastDragAnimation: PetAnimation?
    private var dragSamples: [DragSample] = []

    private struct DragSample {
        var location: CGPoint
        var timestamp: TimeInterval
    }

    private static let dragStartDistance: CGFloat = 4
    private static let dragDirectionThreshold: CGFloat = 0.5
    private static let dragVelocitySampleWindow: TimeInterval = 0.12
    private static let maximumDragVelocitySamples = 8

    var isDragging: Bool {
        dragging
    }

    mutating func start(screenLocation: CGPoint, windowOrigin: CGPoint, timestamp: TimeInterval) {
        active = true
        mouseDownScreenLocation = screenLocation
        previousDragScreenLocation = screenLocation
        mouseDownWindowOrigin = windowOrigin
        dragging = false
        lastDragAnimation = nil
        dragSamples = [DragSample(location: screenLocation, timestamp: timestamp)]
    }

    mutating func drag(to screenLocation: CGPoint, timestamp: TimeInterval) -> PetDragUpdate? {
        guard active else { return nil }
        appendDragSample(location: screenLocation, timestamp: timestamp)
        let delta = CGPoint(
            x: screenLocation.x - mouseDownScreenLocation.x,
            y: screenLocation.y - mouseDownScreenLocation.y
        )

        if !dragging, hypot(delta.x, delta.y) > PetDragTracker.dragStartDistance {
            dragging = true
        }

        let windowOrigin = dragging
            ? CGPoint(
                x: mouseDownWindowOrigin.x + delta.x,
                y: mouseDownWindowOrigin.y + delta.y
            )
            : mouseDownWindowOrigin

        let incrementalX = screenLocation.x - previousDragScreenLocation.x
        previousDragScreenLocation = screenLocation

        let directionChange: PetAnimation?
        if abs(incrementalX) > PetDragTracker.dragDirectionThreshold {
            let animation: PetAnimation = incrementalX >= 0 ? .runningRight : .runningLeft
            if animation != lastDragAnimation {
                lastDragAnimation = animation
                directionChange = animation
            } else {
                directionChange = nil
            }
        } else {
            directionChange = nil
        }

        return PetDragUpdate(
            windowOrigin: windowOrigin,
            isDragging: dragging,
            directionChange: directionChange
        )
    }

    mutating func end(at screenLocation: CGPoint, timestamp: TimeInterval) -> PetDragEnd {
        guard active else {
            return PetDragEnd(wasDragging: false, releaseVelocity: .zero, fallbackAnimation: nil)
        }

        if dragging {
            appendDragSample(location: screenLocation, timestamp: timestamp)
        }

        let result = PetDragEnd(
            wasDragging: dragging,
            releaseVelocity: dragging ? releaseVelocity() : .zero,
            fallbackAnimation: lastDragAnimation
        )
        reset()
        return result
    }

    private mutating func reset() {
        active = false
        dragging = false
        lastDragAnimation = nil
        dragSamples.removeAll(keepingCapacity: true)
    }

    private mutating func appendDragSample(location: CGPoint, timestamp: TimeInterval) {
        dragSamples.append(DragSample(location: location, timestamp: timestamp))
        let minimumTimestamp = timestamp - PetDragTracker.dragVelocitySampleWindow
        dragSamples.removeAll { sample in
            sample.timestamp < minimumTimestamp
        }

        if dragSamples.count > PetDragTracker.maximumDragVelocitySamples {
            dragSamples.removeFirst(dragSamples.count - PetDragTracker.maximumDragVelocitySamples)
        }
    }

    private func releaseVelocity() -> CGVector {
        guard
            let first = dragSamples.first,
            let last = dragSamples.last,
            last.timestamp > first.timestamp
        else {
            return .zero
        }

        let elapsed = last.timestamp - first.timestamp
        return CGVector(
            dx: (last.location.x - first.location.x) / elapsed,
            dy: (last.location.y - first.location.y) / elapsed
        )
    }
}

@MainActor
private final class PetSpriteView: NSView {
    var onClick: (() -> Void)?
    var onDragStart: (() -> Void)?
    var onDragMove: ((CGPoint) -> Void)?
    var onDragDirectionChange: ((PetAnimation) -> Void)?
    var onDragEnd: ((CGVector, PetAnimation?) -> Void)?
    var onInteractionEnd: (() -> Void)?
    var spriteFrame: CGRect {
        didSet {
            needsDisplay = true
        }
    }

    private let spriteSize: CGSize
    private let frameStore: PetSpriteFrameStore
    private var currentFrameAsset: PetSpriteFrameAsset?
    private var dragTracker = PetDragTracker()

    init(
        frame: CGRect,
        spriteSize: CGSize,
        frames: [PetAnimation: [CGImage]]
    ) {
        self.spriteSize = spriteSize
        frameStore = PetSpriteFrameStore(frames: frames, spriteSize: spriteSize)
        let initialAsset = frameStore.asset(for: .idle, frameIndex: 0)
        spriteFrame = CGRect(
            x: frame.width - spriteSize.width - OpenPetsMessageLayout.sideInset,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
        currentFrameAsset = initialAsset
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

    override func hitTest(_ point: NSPoint) -> NSView? {
        guard spriteFrame.contains(point), currentFrameAsset != nil else { return nil }
        return self
    }

    func set(animation: PetAnimation, frameIndex: Int) {
        guard let asset = frameStore.asset(for: animation, frameIndex: frameIndex) else { return }
        currentFrameAsset = asset
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.clear.setFill()
        dirtyRect.fill()

        guard let currentFrameAsset else { return }
        NSGraphicsContext.current?.imageInterpolation = .none
        currentFrameAsset.renderedImage.draw(in: spriteFrame)
    }

    override func mouseDown(with event: NSEvent) {
        onDragStart?()
        dragTracker.start(
            screenLocation: NSEvent.mouseLocation,
            windowOrigin: window?.frame.origin ?? .zero,
            timestamp: event.timestamp
        )
    }

    override func mouseDragged(with event: NSEvent) {
        guard
            let window,
            let update = dragTracker.drag(to: NSEvent.mouseLocation, timestamp: event.timestamp)
        else {
            return
        }
        window.setFrameOrigin(update.windowOrigin)
        onDragMove?(update.windowOrigin)
        if let directionChange = update.directionChange {
            onDragDirectionChange?(directionChange)
        }
    }

    override func mouseUp(with event: NSEvent) {
        let result = dragTracker.end(at: NSEvent.mouseLocation, timestamp: event.timestamp)
        if result.wasDragging {
            onDragEnd?(result.releaseVelocity, result.fallbackAnimation)
        } else {
            onClick?()
        }
        onInteractionEnd?()
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
                    indicator: .waiting
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
                    messages: [PetMessage(threadId: "preview", bubble: bubble)],
                    hiddenMessageCount: isCollapsed ? activeMessageCount : 0,
                    isCollapsed: isCollapsed,
                    activeMessageCount: activeMessageCount,
                    layout: layout,
                    cardFrames: layout.cardFrames,
                    onDismiss: { _ in },
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
