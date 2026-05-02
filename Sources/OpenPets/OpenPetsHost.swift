import AppKit
import CoreGraphics
import Foundation
import ImageIO

public struct OpenPetsHostConfiguration: Sendable {
    public var petDirectoryURL: URL
    public var socketPath: String
    public var scale: CGFloat
    public var positionStoreURL: URL

    public init(
        petDirectoryURL: URL,
        socketPath: String = OpenPetsPaths.defaultSocketPath,
        scale: CGFloat = 1,
        positionStoreURL: URL = OpenPetsPaths.defaultApplicationSupportDirectory.appendingPathComponent("positions.json")
    ) {
        self.petDirectoryURL = petDirectoryURL
        self.socketPath = socketPath
        self.scale = scale
        self.positionStoreURL = positionStoreURL
    }
}

public enum OpenPetsHost {
    @MainActor
    public static func run(configuration: OpenPetsHostConfiguration) throws {
        let petBundle = try PetBundle.load(from: configuration.petDirectoryURL)
        let controller = try PetHostController(
            petBundle: petBundle,
            scale: configuration.scale,
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
    private let petView: PetSpriteView
    private var animationTimer: Timer?
    private var ttlWorkItem: DispatchWorkItem?
    private var messageWorkItem: DispatchWorkItem?
    private var currentAnimation: PetAnimation = .idle
    private var currentFrameIndex = 0
    private var loopCurrentAnimation = true

    init(petBundle: PetBundle, scale: CGFloat, positionStore: PetPositionStore) throws {
        self.petBundle = petBundle
        self.positionStore = positionStore

        let frames = try PetHostController.loadFrames(from: petBundle)
        let spriteSize = CGSize(
            width: CGFloat(petBundle.atlas.cellWidth) * scale,
            height: CGFloat(petBundle.atlas.cellHeight) * scale
        )
        let contentSize = CGSize(width: spriteSize.width, height: spriteSize.height + 72)
        petView = PetSpriteView(frame: CGRect(origin: .zero, size: contentSize), spriteSize: spriteSize, frames: frames)

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
            self?.play(direction, loop: true, ttlSeconds: nil)
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
            if let message {
                setMessage(message, ttlSeconds: ttlSeconds)
            }
            play(animation(forStatusKind: kind), loop: true, ttlSeconds: ttlSeconds)
        case .playAnimation(let name, let loop, let ttlSeconds):
            play(name, loop: loop ?? true, ttlSeconds: ttlSeconds)
        case .clearMessage:
            setMessage(nil, ttlSeconds: nil)
        case .ping, .shutdown:
            break
        }
    }

    func savePosition() {
        try? positionStore.savePosition(window.frame.origin, forPetID: petBundle.manifest.id)
    }

    private func setMessage(_ text: String?, ttlSeconds: Double?) {
        messageWorkItem?.cancel()
        petView.message = text

        guard let ttlSeconds, ttlSeconds > 0, text != nil else { return }
        let workItem = DispatchWorkItem { [weak self] in
            Task { @MainActor in
                self?.petView.message = nil
            }
        }
        messageWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + ttlSeconds, execute: workItem)
    }

    private func play(_ animation: PetAnimation, loop: Bool, ttlSeconds: Double?) {
        ttlWorkItem?.cancel()
        currentAnimation = animation
        currentFrameIndex = 0
        loopCurrentAnimation = loop
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
            if loopCurrentAnimation {
                currentFrameIndex = 0
            } else {
                play(.idle, loop: true, ttlSeconds: nil)
                return
            }
        }

        petView.set(animation: currentAnimation, frameIndex: currentFrameIndex)
        scheduleNextFrame()
    }

    private func animation(forStatusKind kind: String) -> PetAnimation {
        switch kind.lowercased() {
        case "failed", "failure", "error":
            .failed
        case "review", "reviewing", "running", "task", "working":
            .review
        case "done", "success", "completed", "complete":
            .jumping
        case "attention", "reply", "message":
            .waving
        default:
            .idle
        }
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

@MainActor
private final class PetSpriteView: NSView {
    var onClick: (() -> Void)?
    var onDragDirectionChange: ((PetAnimation) -> Void)?
    var onDragEnd: (() -> Void)?

    var message: String? {
        didSet { needsDisplay = true }
    }

    private let spriteSize: CGSize
    private let frames: [PetAnimation: [CGImage]]
    private var currentFrame: CGImage?
    private var mouseDownScreenLocation = CGPoint.zero
    private var mouseDownWindowOrigin = CGPoint.zero
    private var dragging = false
    private var lastDragAnimation: PetAnimation?

    init(frame: CGRect, spriteSize: CGSize, frames: [PetAnimation: [CGImage]]) {
        self.spriteSize = spriteSize
        self.frames = frames
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

        if let message, !message.isEmpty {
            drawMessage(message)
        }

        guard let currentFrame else { return }
        let spriteRect = CGRect(
            x: (bounds.width - spriteSize.width) / 2,
            y: 0,
            width: spriteSize.width,
            height: spriteSize.height
        )
        NSGraphicsContext.current?.imageInterpolation = .none
        NSImage(cgImage: currentFrame, size: spriteSize).draw(in: spriteRect)
    }

    override func mouseDown(with event: NSEvent) {
        mouseDownScreenLocation = NSEvent.mouseLocation
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

        guard abs(delta.x) > 2 else { return }
        let animation: PetAnimation = delta.x >= 0 ? .runningRight : .runningLeft
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

    private func drawMessage(_ message: String) {
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        paragraph.lineBreakMode = .byWordWrapping

        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 13, weight: .medium),
            .foregroundColor: NSColor.labelColor,
            .paragraphStyle: paragraph
        ]
        let maxTextSize = CGSize(width: bounds.width - 24, height: 48)
        let textRect = NSString(string: message).boundingRect(
            with: maxTextSize,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            attributes: attributes
        )
        let bubbleRect = CGRect(
            x: 6,
            y: bounds.height - min(62, textRect.height + 18),
            width: bounds.width - 12,
            height: min(62, textRect.height + 18)
        )

        let path = NSBezierPath(roundedRect: bubbleRect, xRadius: 8, yRadius: 8)
        NSColor.windowBackgroundColor.withAlphaComponent(0.94).setFill()
        path.fill()
        NSColor.separatorColor.withAlphaComponent(0.7).setStroke()
        path.lineWidth = 1
        path.stroke()

        let drawRect = bubbleRect.insetBy(dx: 10, dy: 8)
        NSString(string: message).draw(
            with: drawRect,
            options: [.usesLineFragmentOrigin, .usesFontLeading, .truncatesLastVisibleLine],
            attributes: attributes
        )
    }
}
