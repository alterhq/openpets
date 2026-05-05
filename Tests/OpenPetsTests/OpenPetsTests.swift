import AppKit
import CoreGraphics
import Foundation
import ImageIO
import Logging
import MCP
@testable import OpenPetsCore
@testable import OpenPetsMenuBar
import UniformTypeIdentifiers
import XCTest

final class OpenPetsTests: XCTestCase {
    func testDecodePetManifest() throws {
        let data = Data(
            """
            {
              "id": "starcorn",
              "displayName": "Starcorn",
              "description": "A white chibi unicorn.",
              "spritesheetPath": "spritesheet.webp"
            }
            """.utf8
        )

        let manifest = try JSONDecoder().decode(PetManifest.self, from: data)

        XCTAssertEqual(manifest.id, "starcorn")
        XCTAssertEqual(manifest.displayName, "Starcorn")
        XCTAssertEqual(manifest.spritesheetPath, "spritesheet.webp")
    }

    func testLoadPetBundleDerivesCodexAtlas() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        try Data(
            """
            {
              "id": "test",
              "displayName": "Test",
              "description": "Test pet.",
              "spritesheetPath": "spritesheet.png"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("pet.json"))
        try writePNG(
            width: 1536,
            height: 1872,
            to: directory.appendingPathComponent("spritesheet.png")
        )

        let bundle = try PetBundle.load(from: directory)

        XCTAssertEqual(bundle.atlas.columns, 8)
        XCTAssertEqual(bundle.atlas.rows, 9)
        XCTAssertEqual(bundle.atlas.cellWidth, 192)
        XCTAssertEqual(bundle.atlas.cellHeight, 208)
    }

    func testAnimationRowsAndFrameCounts() {
        XCTAssertEqual(PetAnimation.idle.row, 0)
        XCTAssertEqual(PetAnimation.runningRight.row, 1)
        XCTAssertEqual(PetAnimation.runningLeft.row, 2)
        XCTAssertEqual(PetAnimation.waving.row, 3)
        XCTAssertEqual(PetAnimation.jumping.row, 4)
        XCTAssertEqual(PetAnimation.failed.row, 5)
        XCTAssertEqual(PetAnimation.waiting.row, 6)
        XCTAssertEqual(PetAnimation.running.row, 7)
        XCTAssertEqual(PetAnimation.review.row, 8)

        XCTAssertEqual(PetAnimation.idle.frameCount, 6)
        XCTAssertEqual(PetAnimation.runningRight.frameCount, 8)
        XCTAssertEqual(PetAnimation.runningLeft.frameCount, 8)
        XCTAssertEqual(PetAnimation.waving.frameCount, 4)
        XCTAssertEqual(PetAnimation.jumping.frameCount, 5)
        XCTAssertEqual(PetAnimation.failed.frameCount, 8)
        XCTAssertEqual(PetAnimation.waiting.frameCount, 6)
        XCTAssertEqual(PetAnimation.running.frameCount, 6)
        XCTAssertEqual(PetAnimation.review.frameCount, 6)
    }

    func testIdleAnimationUsesCalmBreathingTiming() {
        let idleLoopDuration = PetAnimation.idle.frameDurationsMilliseconds.reduce(0, +)

        XCTAssertEqual(idleLoopDuration, 8_000)
        XCTAssertGreaterThanOrEqual(PetAnimation.idle.frameDurationsMilliseconds.first ?? 0, 2_000)
        XCTAssertGreaterThanOrEqual(PetAnimation.idle.frameDurationsMilliseconds.last ?? 0, 2_600)
    }

    @MainActor
    func testMessageLayoutKeepsSpriteAnchoredForDifferentMessageWidths() {
        let containerWidth: CGFloat = 316
        let spriteSize = CGSize(width: 112, height: 126)
        let messageAreaHeight: CGFloat = 108
        let expectedRightEdge = containerWidth - OpenPetsMessageLayout.sideInset
        let bubbles = [
            PetBubble(title: "Hi", detail: nil, indicator: .working),
            PetBubble(
                title: "Review ready",
                detail: "Changes are ready to inspect and the message needs enough copy to occupy a wider card.",
                indicator: .attention
            )
        ]

        let layouts = bubbles.map {
            OpenPetsMessageLayout.make(
                bubble: $0,
                isCollapsed: false,
                containerWidth: containerWidth,
                spriteSize: spriteSize,
                messageAreaHeight: messageAreaHeight
            )
        }

        XCTAssertEqual(layouts.first?.spriteFrame.minX, layouts.last?.spriteFrame.minX)
        for layout in layouts {
            XCTAssertEqual(layout.spriteFrame.maxX, expectedRightEdge)
            XCTAssertEqual(layout.cardFrame.maxX, expectedRightEdge)
            XCTAssertGreaterThanOrEqual(layout.cardFrame.minX, OpenPetsMessageLayout.sideInset)
        }
    }

    @MainActor
    func testStackedMessageLayoutShowsFourBubblesAndToggleControl() {
        let messages = (1...5).map { index in
            PetMessage(
                threadId: "thread-\(index)",
                bubble: PetBubble(title: "Message \(index)", detail: nil, indicator: .none)
            )
        }

        let layout = OpenPetsMessageLayout.make(
            messages: Array(messages.suffix(4)),
            hiddenMessageCount: 1,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )

        XCTAssertEqual(layout.cardFrames.count, 4)
        XCTAssertEqual(layout.cardFrames.map(\.maxX), Array(repeating: 304, count: 4))
        XCTAssertGreaterThan(layout.toggleFrame.height, 0)
        XCTAssertEqual(layout.toggleFrame.maxX, 304)
        XCTAssertGreaterThan(layout.containerSize.height, layout.spriteFrame.height)
    }

    @MainActor
    func testMessageLayoutShowsToggleControlForSingleBubble() {
        let layout = OpenPetsMessageLayout.make(
            messages: [
                PetMessage(
                    threadId: "thread-1",
                    bubble: PetBubble(title: "Message", detail: nil, indicator: .none)
                )
            ],
            hiddenMessageCount: 0,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )

        XCTAssertGreaterThan(layout.toggleFrame.height, 0)
        XCTAssertEqual(layout.toggleFrame.maxX, 304)
        XCTAssertGreaterThan(layout.containerSize.height, layout.spriteFrame.height)
    }

    @MainActor
    func testBubbleActionDoesNotIncreaseMessageCardHeight() {
        let plainBubble = PetBubble(title: "Review ready", detail: nil, indicator: .none)
        let actionBubble = PetBubble(
            title: "Review ready",
            detail: nil,
            indicator: .none,
            action: PetBubbleAction(label: "Review", url: try! XCTUnwrap(URL(string: "openpets://review")))
        )
        let plainLayout = OpenPetsMessageLayout.make(
            messages: [PetMessage(threadId: "plain", bubble: plainBubble)],
            hiddenMessageCount: 0,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )
        let actionLayout = OpenPetsMessageLayout.make(
            messages: [PetMessage(threadId: "action", bubble: actionBubble)],
            hiddenMessageCount: 0,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )

        XCTAssertEqual(actionLayout.cardFrame.height, plainLayout.cardFrame.height)
    }

    @MainActor
    func testActionURLOpenerCompletionCanRunOffMainActor() async throws {
        let workspace = FakeWorkspaceOpen()
        let opener = OpenPetsActionURLOpener(workspaceOpen: workspace.open)
        let url = try XCTUnwrap(URL(string: "x-openpets-test://callback?thread=123"))

        opener.open(url)

        XCTAssertEqual(workspace.openedURLs, [url])
        XCTAssertEqual(workspace.activationValues, [true])

        let completion = try XCTUnwrap(workspace.completions.first)
        await Task.detached {
            completion(nil, nil)
        }.value
    }

    func testPetBubbleActionUsesSharedURLOpener() throws {
        let workspace = FakeWorkspaceOpen()
        let opener = OpenPetsActionURLOpener(workspaceOpen: workspace.open)
        let url = try XCTUnwrap(URL(string: "https://example.com/review?id=123"))
        let action = PetBubbleAction(label: "Review", url: url)

        action.open(source: "test", using: opener)
        action.open(source: "test", using: opener)

        XCTAssertEqual(workspace.openedURLs, [url, url])
        XCTAssertEqual(workspace.activationValues, [true, true])
    }

    func testPackagedAppDeclaresOpenPetsIcon() throws {
        let rootURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let plistURL = rootURL.appendingPathComponent("Packaging/OpenPets.app/Contents/Info.plist")
        let iconURL = rootURL.appendingPathComponent("Packaging/OpenPets.app/Contents/Resources/AppIcon.icns")
        let plist = try XCTUnwrap(NSDictionary(contentsOf: plistURL) as? [String: Any])

        XCTAssertEqual(plist["CFBundleIconFile"] as? String, OpenPetsAppIcon.resourceName)
        XCTAssertEqual(plist["CFBundleIconName"] as? String, OpenPetsAppIcon.resourceName)
        XCTAssertGreaterThan(try Data(contentsOf: iconURL).count, 0)
    }

    func testSpriteFrameStoreReusesCachedAssets() throws {
        let image = try makeAlphaTestImage(width: 2, height: 1, alphas: [0, 255])
        let store = PetSpriteFrameStore(frames: [.idle: [image]], spriteSize: CGSize(width: 20, height: 10))

        let first = try XCTUnwrap(store.asset(for: .idle, frameIndex: 0))
        let repeated = try XCTUnwrap(store.asset(for: .idle, frameIndex: 12))

        XCTAssertTrue(first === repeated)
        XCTAssertEqual(first.renderedImage.size, CGSize(width: 20, height: 10))
    }

    func testPetSpriteVisibilityComputesBoundsWithoutDrivingHitTesting() throws {
        let image = try makeAlphaTestImage(width: 3, height: 1, alphas: [0, 255, 0])
        let visibility = try XCTUnwrap(PetSpriteVisibility(image: image))

        XCTAssertEqual(
            visibility.visibleBounds(in: CGRect(x: 10, y: 20, width: 30, height: 10)),
            CGRect(x: 20, y: 20, width: 10, height: 10)
        )
    }

    func testPetDragTrackerMovesWindowOriginByCursorDelta() throws {
        var tracker = PetDragTracker()
        tracker.start(screenLocation: CGPoint(x: 10, y: 20), windowOrigin: CGPoint(x: 100, y: 200), timestamp: 0)

        let update = try XCTUnwrap(tracker.drag(to: CGPoint(x: 32, y: 47), timestamp: 0.05))

        XCTAssertTrue(update.isDragging)
        XCTAssertEqual(update.windowOrigin, CGPoint(x: 122, y: 227))
    }

    func testPetDragTrackerSmallMovementRemainsClick() throws {
        var tracker = PetDragTracker()
        tracker.start(screenLocation: CGPoint(x: 10, y: 20), windowOrigin: CGPoint(x: 100, y: 200), timestamp: 0)

        let update = try XCTUnwrap(tracker.drag(to: CGPoint(x: 12, y: 23), timestamp: 0.02))
        let end = tracker.end(at: CGPoint(x: 12, y: 23), timestamp: 0.03)

        XCTAssertFalse(update.isDragging)
        XCTAssertEqual(update.windowOrigin, CGPoint(x: 100, y: 200))
        XCTAssertFalse(end.wasDragging)
        XCTAssertEqual(end.releaseVelocity, .zero)
    }

    func testPetDragTrackerReleaseReturnsVelocityAndClearsState() throws {
        var tracker = PetDragTracker()
        tracker.start(screenLocation: CGPoint(x: 0, y: 0), windowOrigin: CGPoint(x: 40, y: 50), timestamp: 0)
        _ = try XCTUnwrap(tracker.drag(to: CGPoint(x: 30, y: 0), timestamp: 0.05))

        let end = tracker.end(at: CGPoint(x: 60, y: 0), timestamp: 0.10)

        XCTAssertTrue(end.wasDragging)
        XCTAssertEqual(end.releaseVelocity.dx, 600, accuracy: 0.001)
        XCTAssertEqual(end.releaseVelocity.dy, 0, accuracy: 0.001)
        XCTAssertNil(tracker.drag(to: CGPoint(x: 90, y: 0), timestamp: 0.15))
    }

    func testPetDragTrackerEmitsDirectionChangesOnlyPastThreshold() throws {
        var tracker = PetDragTracker()
        tracker.start(screenLocation: CGPoint(x: 0, y: 0), windowOrigin: .zero, timestamp: 0)

        let mostlyVertical = try XCTUnwrap(tracker.drag(to: CGPoint(x: 0.4, y: 5), timestamp: 0.01))
        let right = try XCTUnwrap(tracker.drag(to: CGPoint(x: 1.2, y: 5), timestamp: 0.02))
        let stillRight = try XCTUnwrap(tracker.drag(to: CGPoint(x: 2.0, y: 5), timestamp: 0.03))
        let left = try XCTUnwrap(tracker.drag(to: CGPoint(x: 1.0, y: 5), timestamp: 0.04))

        XCTAssertNil(mostlyVertical.directionChange)
        XCTAssertEqual(right.directionChange, .runningRight)
        XCTAssertNil(stillRight.directionChange)
        XCTAssertEqual(left.directionChange, .runningLeft)
    }

    @MainActor
    func testMinimalMessageLayoutUsesStablePetBoundsWithoutMessages() {
        let layout = OpenPetsMessageLayout.makeMinimal(
            messages: [],
            hiddenMessageCount: 0,
            spriteSize: CGSize(width: 20, height: 10),
            stableSpriteBounds: CGRect(x: 5, y: 2, width: 10, height: 6),
            messageAreaHeight: 108
        )

        XCTAssertEqual(layout.containerSize, CGSize(width: 10, height: 6))
        XCTAssertEqual(layout.petFrame, CGRect(x: 0, y: 0, width: 10, height: 6))
        XCTAssertEqual(layout.spriteFrame, CGRect(x: -5, y: -2, width: 20, height: 10))
        XCTAssertTrue(layout.cardFrames.isEmpty)
        XCTAssertTrue(layout.toggleFrame.isEmpty)
    }

    @MainActor
    func testBubbleLayoutPreservesPetAnchorDuringResize() {
        let petAnchor = CGPoint(x: 100, y: 200)
        let emptyLayout = OpenPetsMessageLayout.makeMinimal(
            messages: [],
            hiddenMessageCount: 0,
            spriteSize: CGSize(width: 20, height: 10),
            stableSpriteBounds: CGRect(x: 5, y: 2, width: 10, height: 6),
            messageAreaHeight: 108
        )
        let bubbleLayout = OpenPetsMessageLayout.makeMinimal(
            messages: [PetMessage(threadId: "thread-1", bubble: PetBubble(title: "Build running", detail: nil, indicator: .working))],
            hiddenMessageCount: 0,
            spriteSize: CGSize(width: 20, height: 10),
            stableSpriteBounds: CGRect(x: 5, y: 2, width: 10, height: 6),
            messageAreaHeight: 108
        )

        let emptyOrigin = PetWindowPositioning.windowOrigin(preservingPetAnchor: petAnchor, petFrame: emptyLayout.petFrame)
        let bubbleOrigin = PetWindowPositioning.windowOrigin(preservingPetAnchor: petAnchor, petFrame: bubbleLayout.petFrame)

        XCTAssertEqual(CGPoint(x: emptyOrigin.x + emptyLayout.petFrame.minX, y: emptyOrigin.y + emptyLayout.petFrame.minY), petAnchor)
        XCTAssertEqual(CGPoint(x: bubbleOrigin.x + bubbleLayout.petFrame.minX, y: bubbleOrigin.y + bubbleLayout.petFrame.minY), petAnchor)
        XCTAssertGreaterThan(bubbleLayout.containerSize.width, emptyLayout.containerSize.width)
        XCTAssertLessThan(bubbleOrigin.x, emptyOrigin.x)
        XCTAssertGreaterThanOrEqual(bubbleLayout.toggleFrame.minY, 0)
        XCTAssertLessThanOrEqual(bubbleLayout.toggleFrame.maxY, bubbleLayout.containerSize.height)
    }

    @MainActor
    func testMessagePanelLayoutDoesNotIncludePetBoundsInPanelSize() {
        let layout = OpenPetsMessageLayout.makeMessagePanel(
            messages: [PetMessage(threadId: "thread-1", bubble: PetBubble(title: "Build running", detail: nil, indicator: .working))],
            hiddenMessageCount: 0,
            petSize: CGSize(width: 80, height: 100),
            messageAreaHeight: 108
        )
        let panelOrigin = PetWindowPositioning.windowOrigin(
            preservingPetAnchor: CGPoint(x: 300, y: 400),
            petFrame: layout.petFrame
        )

        XCTAssertLessThan(layout.containerSize.height, 100)
        XCTAssertGreaterThanOrEqual(layout.toggleFrame.minY, layout.petFrame.maxY)
        XCTAssertGreaterThanOrEqual(layout.toggleFrame.minY, 0)
        XCTAssertLessThanOrEqual(layout.toggleFrame.maxY, layout.containerSize.height)
        XCTAssertGreaterThanOrEqual(layout.cardFrame.minY, layout.petFrame.maxY)
        XCTAssertGreaterThanOrEqual(layout.cardFrame.minY, 0)
        XCTAssertLessThanOrEqual(layout.cardFrame.maxY, layout.containerSize.height)
        XCTAssertEqual(CGPoint(x: panelOrigin.x + layout.petFrame.minX, y: panelOrigin.y + layout.petFrame.minY), CGPoint(x: 300, y: 400))
    }

    @MainActor
    func testLegacyWindowOriginPositionConvertsToPetAnchor() {
        let legacySize = PetWindowPositioning.legacyContentSize(
            spriteSize: CGSize(width: 20, height: 10),
            messageAreaHeight: 108
        )
        let anchor = PetWindowPositioning.initialPetAnchor(
            storedPosition: StoredPetPosition(CGPoint(x: 10, y: 20), kind: .windowOrigin),
            legacyContentSize: legacySize,
            spriteSize: CGSize(width: 20, height: 10),
            stableSpriteBounds: CGRect(x: 5, y: 2, width: 10, height: 6)
        )

        XCTAssertEqual(anchor, CGPoint(x: 299, y: 22))
    }

    @MainActor
    func testPetHostViewOnlyHitsInteractivePixelsInsideMinimalFrame() throws {
        let image = try makeAlphaTestImage(width: 3, height: 1, alphas: [255, 0, 255])
        let view = PetHostView(
            spriteSize: CGSize(width: 30, height: 10),
            stableSpriteBounds: CGRect(x: 0, y: 0, width: 30, height: 10),
            frames: [.idle: [image]]
        )

        XCTAssertEqual(view.bounds.size, CGSize(width: 30, height: 10))
        XCTAssertNotNil(view.hitTest(CGPoint(x: 5, y: 5)))
        XCTAssertNotNil(view.hitTest(CGPoint(x: 15, y: 5)))
        XCTAssertNotNil(view.hitTest(CGPoint(x: 25, y: 5)))
        XCTAssertNil(view.hitTest(CGPoint(x: 31, y: 5)))
    }

    @MainActor
    func testPetHostViewUsesNarrowStableBoundsWithoutTransparentMargin() throws {
        let image = try makeAlphaTestImage(
            width: 3,
            height: 3,
            alphas: [
                0, 0, 0,
                0, 255, 0,
                0, 0, 0
            ]
        )
        let stableBounds = try XCTUnwrap(PetSpriteVisibility(image: image)?.visibleBounds(
            in: CGRect(x: 0, y: 0, width: 30, height: 30)
        ))
        let view = PetHostView(
            spriteSize: CGSize(width: 30, height: 30),
            stableSpriteBounds: stableBounds,
            frames: [.idle: [image]]
        )

        XCTAssertEqual(stableBounds, CGRect(x: 10, y: 10, width: 10, height: 10))
        XCTAssertEqual(view.bounds.size, CGSize(width: 10, height: 10))
        XCTAssertNotNil(view.hitTest(CGPoint(x: 5, y: 5)))
        XCTAssertNil(view.hitTest(CGPoint(x: 11, y: 5)))
    }

    @MainActor
    func testCollapsedMessageLayoutHidesCardsAndKeepsToggleControl() {
        let messages = (1...3).map { index in
            PetMessage(
                threadId: "thread-\(index)",
                bubble: PetBubble(title: "Message \(index)", detail: nil, indicator: .none)
            )
        }

        let layout = OpenPetsMessageLayout.make(
            messages: messages,
            hiddenMessageCount: 0,
            isCollapsed: true,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )

        XCTAssertTrue(layout.cardFrames.isEmpty)
        XCTAssertGreaterThan(layout.toggleFrame.height, 0)
        XCTAssertEqual(layout.toggleFrame.maxX, 304)
    }

    @MainActor
    func testMessageCloseButtonFitsInsideCard() {
        let layout = OpenPetsMessageLayout.make(
            bubble: PetBubble(title: "Dismiss me", detail: nil, indicator: .none),
            isCollapsed: false,
            containerWidth: 316,
            spriteSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )

        let closeFrame = OpenPetsMessageLayout.closeButtonFrame(in: layout.cardFrame)

        XCTAssertGreaterThan(closeFrame.width, 0)
        XCTAssertGreaterThan(closeFrame.height, 0)
        XCTAssertEqual(closeFrame.minX, layout.cardFrame.minX + OpenPetsMessageLayout.closeButtonInset)
        XCTAssertEqual(closeFrame.maxY, layout.cardFrame.maxY - OpenPetsMessageLayout.closeButtonInset)
        XCTAssertGreaterThanOrEqual(closeFrame.minX, layout.cardFrame.minX)
        XCTAssertGreaterThanOrEqual(closeFrame.minY, layout.cardFrame.minY)
        XCTAssertLessThanOrEqual(closeFrame.maxX, layout.cardFrame.maxX)
        XCTAssertLessThanOrEqual(closeFrame.maxY, layout.cardFrame.maxY)
    }

    @MainActor
    func testMessagePanelHandlesActionBubbleHitInAppKitLayer() throws {
        let actionURL = try XCTUnwrap(URL(string: "ical://"))
        let workspace = FakeWorkspaceOpen()
        let bubble = PetBubble(
            title: "Open Calendar",
            detail: nil,
            indicator: .none,
            action: PetBubbleAction(label: "Open Calendar", url: actionURL)
        )
        let view = PetMessagePanelView(petSize: CGSize(width: 112, height: 126), messageAreaHeight: 108)
        var dismissedThreadIds: [String] = []
        view.onDismissMessage = { dismissedThreadIds.append($0) }
        view.actionURLOpener = OpenPetsActionURLOpener(workspaceOpen: workspace.open)
        view.setBubble(bubble, threadId: "thread-1")
        let layout = OpenPetsMessageLayout.makeMessagePanel(
            messages: [PetMessage(threadId: "thread-1", bubble: bubble)],
            hiddenMessageCount: 0,
            petSize: CGSize(width: 112, height: 126),
            messageAreaHeight: 108
        )
        let window = NSWindow(
            contentRect: CGRect(origin: .zero, size: view.bounds.size),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.contentView = view
        let point = CGPoint(x: layout.cardFrame.midX, y: layout.cardFrame.midY)
        let mouseDown = try XCTUnwrap(mouseEvent(type: .leftMouseDown, location: point, window: window))
        let mouseUp = try XCTUnwrap(mouseEvent(type: .leftMouseUp, location: point, window: window))

        XCTAssertTrue(layout.cardFrame.contains(point))
        XCTAssertTrue(view.hitTest(point) === view)
        XCTAssertTrue(view.acceptsFirstMouse(for: mouseDown))

        view.mouseDown(with: mouseDown)
        view.mouseUp(with: mouseUp)

        XCTAssertEqual(workspace.openedURLs, [actionURL])
        XCTAssertEqual(dismissedThreadIds, ["thread-1"])
    }

    func testMCPToolDescriptionsGuideAgentUsage() throws {
        let tools = openPetsTools()
        let descriptions = Dictionary(uniqueKeysWithValues: tools.map { ($0.name, $0.description ?? "") })

        XCTAssertTrue(descriptions.values.allSatisfy { !$0.isEmpty })
        XCTAssertTrue(try XCTUnwrap(descriptions["get_openpets_status"]).contains("Use this before sending pet commands"))
        XCTAssertTrue(try XCTUnwrap(descriptions["wake_pet"]).contains("when the pet is not running"))
        XCTAssertTrue(try XCTUnwrap(descriptions["stop_pet"]).contains("hide, quit, stop, or dismiss"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("status-driven animation"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("Workflow"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("threadId"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("Different concurrent tasks or agents"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("automatically wakes the pet"))
        XCTAssertTrue(try XCTUnwrap(descriptions["notify"]).contains("returns the current OpenPets status"))
        XCTAssertTrue(try XCTUnwrap(descriptions["play_pet_animation"]).contains("Use notify instead"))
        XCTAssertTrue(try XCTUnwrap(descriptions["clear_pet_message"]).contains("by threadId"))
        XCTAssertTrue(try XCTUnwrap(descriptions["clear_pet_message"]).contains("do not clear another task"))
        XCTAssertTrue(try XCTUnwrap(descriptions["ping_pet"]).contains("connectivity check"))
    }

    func testMCPNotifyAndClearThreadSchemas() throws {
        let threadSchema = try schemaProperty(toolName: "notify", propertyName: "threadId")
        let threadDescription = try XCTUnwrap(threadSchema["description"]?.stringValue)
        XCTAssertEqual(threadSchema["type"]?.stringValue, "string")
        XCTAssertTrue(threadDescription.contains("first notify call"))
        XCTAssertTrue(threadDescription.contains("replaces the right bubble"))

        let clearThreadSchema = try schemaProperty(toolName: "clear_pet_message", propertyName: "threadId")
        XCTAssertEqual(clearThreadSchema["type"]?.stringValue, "string")
        XCTAssertEqual(try schemaRequired(toolName: "clear_pet_message"), ["threadId"])

        let urlSchema = try schemaProperty(toolName: "notify", propertyName: "url")
        let urlDescription = try XCTUnwrap(urlSchema["description"]?.stringValue)
        XCTAssertEqual(urlSchema["type"]?.stringValue, "string")
        XCTAssertTrue(urlDescription.contains("Optional URL"))
    }

    func testMCPNotifyResultReturnsThreadStructuredContent() throws {
        let threadId = "11111111-1111-4111-8111-111111111111"
        let result = commandResult(PetResponse(ok: true, threadId: threadId))

        XCTAssertFalse(result.isError ?? false)
        let text: String
        if case let .text(value, _, _) = try XCTUnwrap(result.content.first) {
            text = value
        } else {
            XCTFail("Expected text tool content")
            return
        }
        XCTAssertTrue(text.contains("threadId: \(threadId)"))
        XCTAssertTrue(text.contains("Use this threadId on your next notify call"))
        XCTAssertTrue(text.contains("updates the existing bubble"))
        XCTAssertEqual(result.structuredContent?.objectValue?["threadId"]?.stringValue, threadId)
    }

    func testMCPHTTPPostWithExpiredSessionFallsBackToStatelessHandling() async throws {
        let app = OpenPetsMCPHTTPApp(
            configuration: .init(host: "127.0.0.1", port: 3001, endpoint: "/mcp"),
            serverFactory: { _ in
                let server = Server(
                    name: "openpets-test",
                    version: "1.0.0",
                    capabilities: .init(tools: .init(listChanged: true))
                )
                await server.withMethodHandler(ListTools.self) { _ in
                    .init(tools: [])
                }
                return server
            },
            logger: Logger(label: "openpets.tests")
        )
        let request = HTTPRequest(
            method: "POST",
            headers: [
                HTTPHeaderName.accept: "application/json, text/event-stream",
                HTTPHeaderName.contentType: "application/json",
                HTTPHeaderName.sessionID: "expired-session"
            ],
            body: Data(#"{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}"#.utf8),
            path: "/mcp"
        )

        let response = await app.handleHTTPRequest(request)

        XCTAssertEqual(response.statusCode, 200)
        let bodyData = try XCTUnwrap(response.bodyData)
        let body = try XCTUnwrap(String(data: bodyData, encoding: .utf8))
        XCTAssertTrue(body.contains(#""tools":[]"#))
    }

    func testMCPNotifyStatusSchemaListsValidStatuses() throws {
        let statusSchema = try schemaProperty(toolName: "notify", propertyName: "status")
        let description = try XCTUnwrap(statusSchema["description"]?.stringValue)
        let enumValues = try XCTUnwrap(statusSchema["enum"]?.arrayValue?.compactMap(\.stringValue))

        XCTAssertEqual(enumValues, openPetsStatusValues)
        for status in openPetsStatusValues {
            XCTAssertTrue(description.contains(status))
        }
        XCTAssertFalse(description.contains("answer"))
        XCTAssertFalse(enumValues.contains("task"))
        XCTAssertFalse(enumValues.contains("working"))
        XCTAssertFalse(enumValues.contains("reviewing"))
        XCTAssertFalse(enumValues.contains("success"))
        XCTAssertFalse(enumValues.contains("queued"))
        XCTAssertFalse(enumValues.contains("reply"))
    }

    func testMCPAnimationSchemaListsValidAnimationNames() throws {
        let animationSchema = try schemaProperty(toolName: "play_pet_animation", propertyName: "name")
        let description = try XCTUnwrap(animationSchema["description"]?.stringValue)
        let enumValues = try XCTUnwrap(animationSchema["enum"]?.arrayValue?.compactMap(\.stringValue))

        XCTAssertEqual(enumValues, openPetsAnimationValues)
        for animation in openPetsAnimationValues {
            XCTAssertTrue(description.contains(animation))
        }
        XCTAssertTrue(description.contains("runningRight"))
        XCTAssertTrue(description.contains("runningLeft"))
    }

    func testMessageStatusDoesNotUseProgressIndicator() {
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "message"), .none)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "reply"), .none)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "attention"), .none)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "running"), .working)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "waiting"), .waiting)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "review"), .review)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "reviewing"), .review)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "done"), .success)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "fail"), .attention)
        XCTAssertEqual(openPetsBubbleIndicator(forStatusKind: "failed"), .attention)
    }

    func testDefaultDisplayConfigurationUsesSmallScale() {
        XCTAssertEqual(OpenPetsDisplayConfiguration.default.scale, 0.42)

        let configuration = OpenPetsHostConfiguration(
            petDirectoryURL: URL(fileURLWithPath: "/tmp/example-pet")
        )
        XCTAssertEqual(configuration.display, .default)
        XCTAssertEqual(configuration.scale, 0.42)
        XCTAssertEqual(configuration.positionStoreURL.path, OpenPetsPaths.defaultPositionStoreURL.path)
    }

    func testOpenPetsConfigurationSavesAndLoadsUserDefaults() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("config.json")
        let configuration = OpenPetsConfiguration(
            display: OpenPetsDisplayConfiguration(scale: 0.25, messageAreaHeight: 44),
            socketPath: "/tmp/openpets-test.sock",
            mcpHost: "0.0.0.0",
            mcpPort: 3999,
            mcpEndpoint: "/custom-mcp"
        )

        try configuration.save(to: url)
        let reloaded = try OpenPetsConfiguration.load(from: url)

        XCTAssertEqual(reloaded, configuration)
    }

    func testOpenPetsConfigurationLoadOrCreateDefault() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let url = directory.appendingPathComponent("nested/config.json")

        let configuration = try OpenPetsConfiguration.loadOrCreateDefault(at: url)

        XCTAssertEqual(configuration.display, .default)
        XCTAssertEqual(configuration.socketPath, OpenPetsPaths.defaultSocketPath)
        XCTAssertEqual(configuration.mcpHost, "127.0.0.1")
        XCTAssertEqual(configuration.mcpPort, 3001)
        XCTAssertEqual(configuration.mcpEndpoint, "/mcp")
        XCTAssertEqual(configuration.activePetID, OpenPetsBundledPets.starcornID)
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    }

    func testOpenPetsConfigurationDecodesLegacyFiles() throws {
        let data = Data(
            """
            {
              "display": {
                "scale": 0.31,
                "messageAreaHeight": 48
              },
              "socketPath": "/tmp/openpets-legacy.sock"
            }
            """.utf8
        )

        let configuration = try JSONDecoder().decode(OpenPetsConfiguration.self, from: data)

        XCTAssertEqual(configuration.display, OpenPetsDisplayConfiguration(scale: 0.31, messageAreaHeight: 48))
        XCTAssertEqual(configuration.socketPath, "/tmp/openpets-legacy.sock")
        XCTAssertEqual(configuration.mcpHost, "127.0.0.1")
        XCTAssertEqual(configuration.mcpPort, 3001)
        XCTAssertEqual(configuration.mcpEndpoint, "/mcp")
        XCTAssertEqual(configuration.activePetID, OpenPetsBundledPets.starcornID)
    }

    func testBundledStarcornPetLoads() throws {
        let bundle = try PetBundle.load(from: OpenPetsBundledPets.starcornURL)

        XCTAssertEqual(bundle.manifest.id, "starcorn")
        XCTAssertEqual(bundle.manifest.displayName, "Starcorn")
    }

    func testPetLibraryDiscoversInstalledAndKnownUserPetDirectories() throws {
        let root = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let installedURL = root.appendingPathComponent("Installed", isDirectory: true)
        let codexURL = root.appendingPathComponent(".codex/pets", isDirectory: true)
        let configURL = root.appendingPathComponent(".config/openpets", isDirectory: true)
        let installedPetURL = installedURL.appendingPathComponent("installed-pet-renamed", isDirectory: true)
        try makePetBundle(id: "installed-pet", at: installedPetURL)
        try makePetBundle(
            id: "codex-pet",
            at: codexURL.appendingPathComponent("codex-pet", isDirectory: true)
        )
        try makePetBundle(id: "config-pet", at: configURL)
        try makePetBundle(
            id: "installed-pet",
            at: codexURL.appendingPathComponent("installed-pet", isDirectory: true)
        )

        let library = OpenPetsPetLibrary(
            installedPetsDirectory: installedURL,
            discoveredPetsDirectories: [codexURL, configURL]
        )
        let pets = library.listPets()

        XCTAssertEqual(
            pets.map(\.id),
            [OpenPetsBundledPets.starcornID, "installed-pet", "codex-pet", "config-pet"]
        )
        XCTAssertEqual(
            library.petURL(for: "installed-pet")?.standardizedFileURL.path,
            installedPetURL.standardizedFileURL.path
        )
        XCTAssertEqual(
            library.petURL(for: "codex-pet")?.standardizedFileURL.path,
            codexURL.appendingPathComponent("codex-pet", isDirectory: true).standardizedFileURL.path
        )
        XCTAssertEqual(
            library.petURL(for: "config-pet")?.standardizedFileURL.path,
            configURL.standardizedFileURL.path
        )
    }

    func testInstallDeepLinkParsesDownloadURLAndPetID() throws {
        let request = try OpenPetsInstallRequest.parseDeepLink(URL(string: "openpets://install?url=https%3A%2F%2Fopenpets.sh%2Fapi%2Fpets%2Fstarcorn%2Fdownload%3Fticket%3Dabc&id=starcorn")!)

        XCTAssertEqual(request.downloadURL.absoluteString, "https://openpets.sh/api/pets/starcorn/download?ticket=abc")
        XCTAssertEqual(request.requestedPetID, "starcorn")
    }

    func testPetInstallerInstallsAndActivatesValidBundle() throws {
        let root = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let sourceBundle = root.appendingPathComponent("test-pet", isDirectory: true)
        let archiveURL = root.appendingPathComponent("test-pet.zip")
        let installedURL = root.appendingPathComponent("Installed", isDirectory: true)
        let configURL = root.appendingPathComponent("config/config.json")
        try makePetBundle(id: "test-pet", at: sourceBundle)
        try zipDirectory(sourceBundle, to: archiveURL)

        let result = try OpenPetsPetInstaller(
            installedPetsDirectory: installedURL,
            configurationURL: configURL
        ).install(
            request: OpenPetsInstallRequest(downloadURL: archiveURL, requestedPetID: "test-pet"),
            activate: true
        )

        XCTAssertEqual(result.petID, "test-pet")
        XCTAssertTrue(FileManager.default.fileExists(atPath: installedURL.appendingPathComponent("test-pet/pet.json").path))
        XCTAssertEqual(try OpenPetsConfiguration.load(from: configURL).activePetID, "test-pet")
    }

    func testPetInstallerRejectsUnsafeArchiveEntry() throws {
        let root = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let work = root.appendingPathComponent("work", isDirectory: true)
        let archiveURL = root.appendingPathComponent("unsafe.zip")
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        try Data("unsafe".utf8).write(to: root.appendingPathComponent("evil.txt"))
        _ = try runProcess("/usr/bin/zip", arguments: ["-q", archiveURL.path, "../evil.txt"], workingDirectory: work)

        XCTAssertThrowsError(try OpenPetsPetInstaller(
            installedPetsDirectory: root.appendingPathComponent("Installed", isDirectory: true),
            configurationURL: root.appendingPathComponent("config.json")
        ).install(request: OpenPetsInstallRequest(downloadURL: archiveURL))) { error in
            XCTAssertEqual(error as? OpenPetsInstallError, .unsafeArchiveEntry("../evil.txt"))
        }
    }

    func testPetCommandRoundTripCoding() throws {
        let commands: [PetCommand] = [
            .notify(PetNotification(
                title: "Review ready",
                text: "Changes are ready to inspect.",
                status: "review",
                threadId: "11111111-1111-4111-8111-111111111111",
                url: "https://example.com/review?id=123",
                buttonLabel: "Review",
                ttlSeconds: 30
            )),
            .playAnimation(name: .waving, loop: false, ttlSeconds: 1),
            .clearMessage(threadId: "11111111-1111-4111-8111-111111111111"),
            .ping,
            .shutdown
        ]

        for command in commands {
            let data = try JSONEncoder().encode(command)
            let decoded = try JSONDecoder().decode(PetCommand.self, from: data)
            XCTAssertEqual(decoded, command)
        }
    }

    func testPetNotificationUsesURLCodingKey() throws {
        let notification = PetNotification(
            title: "Reply needed",
            text: "A user asked a follow-up.",
            status: "reply",
            url: "https://example.com/reply?id=42",
            buttonLabel: "Reply",
            ttlSeconds: 10
        )

        let data = try JSONEncoder().encode(notification)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["url"] as? String, "https://example.com/reply?id=42")
        XCTAssertNil(json["x-url-callback"])

        let decoded = try JSONDecoder().decode(PetNotification.self, from: data)
        XCTAssertEqual(decoded, notification)
    }

    func testPetResponseRoundTripCodingIncludesThreadId() throws {
        let response = PetResponse(
            ok: true,
            message: "created",
            threadId: "11111111-1111-4111-8111-111111111111"
        )

        let data = try JSONEncoder().encode(response)
        let decoded = try JSONDecoder().decode(PetResponse.self, from: data)

        XCTAssertEqual(decoded, response)
    }

    func testPetMessageStackUpdatesClearsAndCapsVisibleMessages() {
        var stack = PetMessageStack()

        stack.setBubble(PetBubble(title: "One", detail: nil, indicator: .working), threadId: "one")
        stack.setBubble(PetBubble(title: "Two", detail: nil, indicator: .success), threadId: "two")
        stack.setBubble(PetBubble(title: "One updated", detail: "Still running", indicator: .working), threadId: "one")

        XCTAssertEqual(stack.activeMessages.map(\.threadId), ["one", "two"])
        XCTAssertEqual(stack.activeMessages.first?.bubble.title, "One updated")
        XCTAssertEqual(stack.activeMessages.last?.bubble.title, "Two")

        stack.clearBubble(threadId: "two")

        XCTAssertEqual(stack.activeMessages.map(\.threadId), ["one"])

        for index in 2...6 {
            stack.setBubble(
                PetBubble(title: "Message \(index)", detail: nil, indicator: .none),
                threadId: "thread-\(index)"
            )
        }

        XCTAssertEqual(stack.activeCount, 6)
        XCTAssertEqual(stack.visibleMessages().map(\.threadId), ["thread-3", "thread-4", "thread-5", "thread-6"])
        XCTAssertEqual(stack.hiddenMessageCount(), 2)
    }

    func testUnixSocketClientServerFraming() throws {
        let socketPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-\(UUID().uuidString).sock")
            .path
        let server = OpenPetsServer(socketPath: socketPath) { command in
            switch command {
            case .ping:
                PetResponse(ok: true, message: "pong")
            case .notify(let notification):
                PetResponse(ok: true, message: notification.title)
            default:
                PetResponse(ok: false, message: "unexpected")
            }
        }
        try server.start()
        defer { server.stop() }

        let client = OpenPetsClient(socketPath: socketPath)
        XCTAssertEqual(try client.send(.ping), PetResponse(ok: true, message: "pong"))
        XCTAssertEqual(
            try client.send(.notify(PetNotification(title: "Hello", text: "hello", status: "message"))),
            PetResponse(ok: true, message: "Hello")
        )
    }

    func testCommandLineToolInstallerCreatesUserShim() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let executableURL = directory.appendingPathComponent("openpets-cli")
        let installDirectoryURL = directory.appendingPathComponent("bin", isDirectory: true)
        try Data("#!/bin/sh\n".utf8).write(to: executableURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)

        let installedURL = try OpenPetsCommandLineToolInstaller(
            bundledExecutableURL: executableURL,
            installDirectoryURL: installDirectoryURL
        ).install()

        XCTAssertEqual(installedURL.lastPathComponent, "openpets")
        XCTAssertEqual(
            try FileManager.default.destinationOfSymbolicLink(atPath: installedURL.path),
            executableURL.path
        )
    }

    func testCommandLineToolInstallerDoesNotOverwriteRegularFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let executableURL = directory.appendingPathComponent("openpets-cli")
        let installDirectoryURL = directory.appendingPathComponent("bin", isDirectory: true)
        let destinationURL = installDirectoryURL.appendingPathComponent("openpets")
        try Data("#!/bin/sh\n".utf8).write(to: executableURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)
        try FileManager.default.createDirectory(at: installDirectoryURL, withIntermediateDirectories: true)
        try Data("existing".utf8).write(to: destinationURL)

        XCTAssertThrowsError(try OpenPetsCommandLineToolInstaller(
            bundledExecutableURL: executableURL,
            installDirectoryURL: installDirectoryURL
        ).install()) { error in
            XCTAssertEqual(error as? OpenPetsCommandLineToolInstallerError, .destinationExists(destinationURL))
        }
        XCTAssertEqual(try String(contentsOf: destinationURL), "existing")
    }

    func testCommandLineToolInstallerDoesNotReplaceUnownedSymlink() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let executableURL = directory.appendingPathComponent("openpets-cli")
        let installDirectoryURL = directory.appendingPathComponent("bin", isDirectory: true)
        let destinationURL = installDirectoryURL.appendingPathComponent("openpets")
        let otherToolURL = directory.appendingPathComponent("other-openpets")
        try Data("#!/bin/sh\n".utf8).write(to: executableURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)
        try FileManager.default.createDirectory(at: installDirectoryURL, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: destinationURL, withDestinationURL: otherToolURL)

        XCTAssertThrowsError(try OpenPetsCommandLineToolInstaller(
            bundledExecutableURL: executableURL,
            installDirectoryURL: installDirectoryURL
        ).install()) { error in
            XCTAssertEqual(error as? OpenPetsCommandLineToolInstallerError, .destinationExists(destinationURL))
        }
        XCTAssertEqual(
            try FileManager.default.destinationOfSymbolicLink(atPath: destinationURL.path),
            otherToolURL.path
        )
    }

    func testCommandLineToolInstallerReplacesOpenPetsShim() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let executableURL = directory.appendingPathComponent("openpets-cli")
        let installDirectoryURL = directory.appendingPathComponent("bin", isDirectory: true)
        let destinationURL = installDirectoryURL.appendingPathComponent("openpets")
        let previousExecutableURL = directory
            .appendingPathComponent("OpenPets.app", isDirectory: true)
            .appendingPathComponent("Contents", isDirectory: true)
            .appendingPathComponent("MacOS", isDirectory: true)
            .appendingPathComponent("openpets-cli")
        try Data("#!/bin/sh\n".utf8).write(to: executableURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)
        try FileManager.default.createDirectory(at: installDirectoryURL, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: destinationURL, withDestinationURL: previousExecutableURL)

        _ = try OpenPetsCommandLineToolInstaller(
            bundledExecutableURL: executableURL,
            installDirectoryURL: installDirectoryURL
        ).install()

        XCTAssertEqual(
            try FileManager.default.destinationOfSymbolicLink(atPath: destinationURL.path),
            executableURL.path
        )
    }

    func testFirstLaunchCreatesConfigWithNextAvailableMCPPort() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configurationURL = directory.appendingPathComponent("config/openpets.json")

        let didPrepare = try OpenPetsFirstLaunch.prepareConfigurationIfNeeded(
            configurationURL: configurationURL,
            portAllocator: OpenPetsMCPPortAllocator(
                portChecker: FakePortChecker(availablePorts: [3003]),
                maximumPort: 3005
            )
        )

        XCTAssertTrue(didPrepare)
        let configuration = try OpenPetsConfiguration.load(from: configurationURL)
        XCTAssertEqual(configuration.mcpHost, "127.0.0.1")
        XCTAssertEqual(configuration.mcpPort, 3003)
        XCTAssertEqual(configuration.mcpEndpoint, "/mcp")
    }

    func testFirstLaunchDoesNotRewriteExistingConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configurationURL = directory.appendingPathComponent("config/openpets.json")
        try OpenPetsConfiguration(mcpPort: 3999).save(to: configurationURL)

        let didPrepare = try OpenPetsFirstLaunch.prepareConfigurationIfNeeded(
            configurationURL: configurationURL,
            portAllocator: OpenPetsMCPPortAllocator(
                portChecker: FakePortChecker(availablePorts: [3003]),
                maximumPort: 3005
            )
        )

        XCTAssertFalse(didPrepare)
        XCTAssertEqual(try OpenPetsConfiguration.load(from: configurationURL).mcpPort, 3999)
    }

    func testAgentDetectorFindsConfiguredCodexAndMissingClaude() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let binURL = directory.appendingPathComponent("bin", isDirectory: true)
        let codexURL = try makeExecutable(named: "codex", in: binURL)
        let codexConfigURL = directory.appendingPathComponent("codex/config.toml")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try FileManager.default.createDirectory(at: codexConfigURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("[mcp_servers.openpets]\nurl = \"\(mcpURL)\"\n".utf8).write(to: codexConfigURL)
        let runner = FakeProcessRunner(responses: [
            FakeProcessRunner.key("/bin/zsh", ["-lc", "command -v claude"]): .failure("not found")
        ])

        let detections = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [binURL],
            codexConfigurationURL: codexConfigURL
        ).detectAll(mcpURL: mcpURL)

        XCTAssertEqual(detections.first { $0.kind == .codex }?.state, .configured)
        XCTAssertEqual(detections.first { $0.kind == .codex }?.executableURL?.path, codexURL.path)
        XCTAssertEqual(detections.first { $0.kind == .claude }?.state, .missing)
        XCTAssertFalse(runner.recordedInvocations.contains(FakeProcessRunner.key(codexURL.path, ["mcp", "get", "openpets"])))
    }

    func testAgentDetectorDoesNotRunClaudeMCPGetDuringDetection() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let claudeURL = try makeExecutable(named: "claude", in: directory)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            claudeConfigurationURL: directory.appendingPathComponent("missing-claude.json")
        ).detect(.claude, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .installed)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
        XCTAssertFalse(runner.recordedInvocations.contains(FakeProcessRunner.key(claudeURL.path, ["mcp", "get", "openpets"])))
    }

    func testAgentDetectorReportsDifferentConfiguredCodexURL() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let binURL = directory.appendingPathComponent("bin", isDirectory: true)
        let codexURL = try makeExecutable(named: "codex", in: binURL)
        let codexConfigURL = directory.appendingPathComponent("codex/config.toml")
        try FileManager.default.createDirectory(at: codexConfigURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("[mcp_servers.openpets]\nurl = \"http://127.0.0.1:3001/mcp\"\n".utf8).write(to: codexConfigURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [binURL],
            codexConfigurationURL: codexConfigURL
        ).detect(.codex, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertFalse(runner.recordedInvocations.contains(FakeProcessRunner.key(codexURL.path, ["mcp", "get", "openpets"])))
    }

    func testAgentDetectorFindsConfiguredClaudeUserMCP() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let claudeURL = try makeExecutable(named: "claude", in: directory)
        let claudeConfigURL = directory.appendingPathComponent(".claude.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeClaudeConfig(to: claudeConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            claudeConfigurationURL: claudeConfigURL
        ).detect(.claude, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertEqual(detection.executableURL?.path, claudeURL.path)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsDifferentConfiguredClaudeURL() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "claude", in: directory)
        let claudeConfigURL = directory.appendingPathComponent(".claude.json")
        try writeClaudeConfig(to: claudeConfigURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            claudeConfigurationURL: claudeConfigURL
        ).detect(.claude, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorUsesNonInteractiveShellOnlyAfterFastPathMissesTool() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let codexURL = try makeExecutable(named: "codex", in: directory)
        let runner = FakeProcessRunner(responses: [
            FakeProcessRunner.key("/bin/zsh", ["-lc", "command -v codex"]): .success("\(codexURL.path)\n")
        ])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [],
            codexConfigurationURL: directory.appendingPathComponent("missing-config.toml")
        ).detect(.codex, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .installed)
        XCTAssertEqual(detection.executableURL?.path, codexURL.path)
        XCTAssertEqual(runner.recordedInvocations, [FakeProcessRunner.key("/bin/zsh", ["-lc", "command -v codex"])])
    }

    func testAgentDetectorFallsBackToKnownSearchDirectory() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let executableURL = try makeExecutable(named: "claude", in: directory)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            claudeConfigurationURL: directory.appendingPathComponent("missing-claude.json")
        ).detect(.claude, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .installed)
        XCTAssertEqual(detection.executableURL?.path, executableURL.path)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsSetupPathAvailability() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "codex", in: directory)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory]
        ).detect(.codex, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertTrue(detection.setupPathsAvailable)
        XCTAssertTrue(detection.detail.contains(".codex"))
    }

    func testAgentSetupInstallerBuildsCommandsWithActiveMCPURL() {
        let installer = OpenPetsAgentSetupInstaller(processRunner: FakeProcessRunner(responses: [:]))
        let mcpURL = "http://127.0.0.1:3010/mcp"

        XCTAssertEqual(
            installer.command(
                kind: .codex,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/codex"),
                mcpURL: mcpURL
            ).arguments,
            ["mcp", "add", "openpets", "--url", mcpURL]
        )
        XCTAssertEqual(
            installer.command(
                kind: .claude,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/claude"),
                mcpURL: mcpURL
            ).arguments,
            ["mcp", "add", "--transport", "http", "--scope", "user", "openpets", mcpURL]
        )
    }

    func testAgentSetupInstallerBuildsUninstallCommands() {
        let installer = OpenPetsAgentSetupInstaller(processRunner: FakeProcessRunner(responses: [:]))

        XCTAssertEqual(
            installer.uninstallCommand(
                kind: .codex,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/codex")
            ).arguments,
            ["mcp", "remove", "openpets"]
        )
        XCTAssertEqual(
            installer.uninstallCommand(
                kind: .claude,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/claude")
            ).arguments,
            ["mcp", "remove", "--scope", "user", "openpets"]
        )
    }

    func testDefaultProcessRunnerAddsExecutableDirectoryToPATH() {
        let executableURL = URL(fileURLWithPath: "/Users/sam/.nvm/versions/node/v22.17.0/bin/codex")

        let environment = OpenPetsDefaultProcessRunner.environment(
            for: executableURL,
            baseEnvironment: ["PATH": "/usr/bin:/bin"]
        )
        let pathDirectories = environment["PATH"]?.split(separator: ":").map(String.init)

        XCTAssertEqual(pathDirectories?.first, "/Users/sam/.nvm/versions/node/v22.17.0/bin")
        XCTAssertTrue(pathDirectories?.contains("/usr/bin") == true)
        XCTAssertTrue(pathDirectories?.contains("/bin") == true)
    }

    func testAgentSetupInstallerReturnsProcessResult() throws {
        let commandKey = FakeProcessRunner.key(
            "/usr/local/bin/codex",
            ["mcp", "add", "openpets", "--url", "http://127.0.0.1:3010/mcp"]
        )
        let installer = OpenPetsAgentSetupInstaller(processRunner: FakeProcessRunner(responses: [
            commandKey: .failure("codex failed")
        ]))

        let result = try installer.install(
            kind: .codex,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/codex"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertFalse(result.succeeded)
        XCTAssertEqual(result.message, "codex failed")
    }

    func testAgentSetupInstallerReturnsUninstallResult() throws {
        let commandKey = FakeProcessRunner.key(
            "/usr/local/bin/claude",
            ["mcp", "remove", "--scope", "user", "openpets"]
        )
        let installer = OpenPetsAgentSetupInstaller(processRunner: FakeProcessRunner(responses: [
            commandKey: .success("removed")
        ]))

        let result = try installer.uninstall(
            kind: .claude,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/claude")
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertEqual(result.operation, .uninstall)
        XCTAssertEqual(result.message, "Claude Code MCP setup removed.")
    }

    func testAssistantInstructionsSnippetMatchesSharedGuidance() {
        let snippet = OpenPetsAssistantInstructions.snippet

        XCTAssertTrue(snippet.contains("## OpenPets MCP"))
        XCTAssertTrue(snippet.contains("call `notify`"))
        XCTAssertTrue(snippet.contains("call `wake_pet` and retry `notify` once"))
        XCTAssertTrue(snippet.contains("Do not notify for greetings"))
    }

    func testAssistantInstructionsAppendCreatesFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("AGENTS.md")

        try OpenPetsAssistantInstructions.appendSnippet(to: fileURL)

        let contents = try String(contentsOf: fileURL)
        XCTAssertTrue(contents.contains("## OpenPets MCP"))
        XCTAssertTrue(contents.contains("call `notify`"))
    }

    func testAssistantInstructionsAppendIsIdempotent() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileURL = directory.appendingPathComponent("CLAUDE.md")

        try OpenPetsAssistantInstructions.appendSnippet(to: fileURL)
        try OpenPetsAssistantInstructions.appendSnippet(to: fileURL)

        let contents = try String(contentsOf: fileURL)
        XCTAssertEqual(contents.components(separatedBy: "## OpenPets MCP").count, 2)
    }

    func testOpenPetsClientReportsRunningPet() throws {
        let socketPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-\(UUID().uuidString).sock")
            .path
        let client = OpenPetsClient(socketPath: socketPath)
        XCTAssertFalse(client.isPetRunning())

        let server = OpenPetsServer(socketPath: socketPath) { command in
            switch command {
            case .ping:
                PetResponse(ok: true, message: "pong")
            default:
                PetResponse(ok: false, message: "unexpected")
            }
        }
        try server.start()
        defer { server.stop() }

        XCTAssertTrue(client.isPetRunning())
    }

    func testOpenPetsServerDoesNotReplaceLiveSocket() throws {
        let socketPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-\(UUID().uuidString).sock")
            .path
        let firstServer = OpenPetsServer(socketPath: socketPath) { command in
            switch command {
            case .ping:
                PetResponse(ok: true, message: "first")
            default:
                PetResponse(ok: false, message: "unexpected")
            }
        }
        try firstServer.start()
        defer { firstServer.stop() }

        do {
            let secondServer = OpenPetsServer(socketPath: socketPath) { command in
                switch command {
                case .ping:
                    PetResponse(ok: true, message: "second")
                default:
                    PetResponse(ok: false, message: "unexpected")
                }
            }

            XCTAssertThrowsError(try secondServer.start()) { error in
                XCTAssertEqual(error as? OpenPetsError, .socketAlreadyInUse(socketPath))
            }
            secondServer.stop()
        }
        XCTAssertEqual(
            try OpenPetsClient(socketPath: socketPath).send(.ping),
            PetResponse(ok: true, message: "first")
        )
    }

    func testPositionStorePersistsPerPet() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let storeURL = directory.appendingPathComponent("positions.json")
        let store = PetPositionStore(url: storeURL)

        try store.savePosition(CGPoint(x: 12, y: 34), forPetID: "starcorn")

        let reloaded = PetPositionStore(url: storeURL)
        XCTAssertEqual(reloaded.loadPosition(forPetID: "starcorn"), CGPoint(x: 12, y: 34))
        XCTAssertEqual(reloaded.loadStoredPosition(forPetID: "starcorn")?.kind, .petAnchor)
        XCTAssertNil(reloaded.loadPosition(forPetID: "other"))
    }

    func testStoredPetPositionDecodesLegacyWindowOrigin() throws {
        let position = try JSONDecoder().decode(StoredPetPosition.self, from: Data(#"{"x":12,"y":34}"#.utf8))

        XCTAssertEqual(position.point, CGPoint(x: 12, y: 34))
        XCTAssertEqual(position.kind, .windowOrigin)
    }

    func testPetLaunchMotionRequiresStrongRelease() {
        XCTAssertTrue(PetLaunchMotion.shouldLaunch(velocity: CGVector(dx: 650, dy: 0)))
        XCTAssertTrue(PetLaunchMotion.shouldLaunch(velocity: CGVector(dx: 500, dy: 500)))
        XCTAssertFalse(PetLaunchMotion.shouldLaunch(velocity: CGVector(dx: 300, dy: 200)))
    }

    func testPetLaunchMotionSelectsDirectionFromHorizontalVelocity() {
        XCTAssertEqual(
            PetLaunchMotion.animation(for: CGVector(dx: 20, dy: 900), fallback: .runningLeft),
            .runningRight
        )
        XCTAssertEqual(
            PetLaunchMotion.animation(for: CGVector(dx: -20, dy: 900), fallback: .runningRight),
            .runningLeft
        )
        XCTAssertEqual(
            PetLaunchMotion.animation(for: CGVector(dx: 0, dy: 900), fallback: .runningLeft),
            .runningLeft
        )
    }

    func testPetLaunchMotionDecaysVelocityAndKeepsMoving() {
        let step = PetLaunchMotion.step(
            origin: CGPoint(x: 100, y: 100),
            velocity: CGVector(dx: 900, dy: 300),
            movingFrame: CGRect(x: 0, y: 0, width: 80, height: 80),
            visibleFrame: CGRect(x: 0, y: 0, width: 1_000, height: 800),
            fallbackAnimation: .runningRight,
            deltaTime: PetLaunchMotion.frameInterval
        )

        XCTAssertGreaterThan(step.origin.x, 100)
        XCTAssertGreaterThan(step.origin.y, 100)
        XCTAssertLessThan(hypot(step.velocity.dx, step.velocity.dy), hypot(900, 300))
        XCTAssertFalse(step.shouldStop)
    }

    func testPetLaunchMotionStopsBelowThreshold() {
        let step = PetLaunchMotion.step(
            origin: CGPoint(x: 100, y: 100),
            velocity: CGVector(dx: 20, dy: 10),
            movingFrame: CGRect(x: 0, y: 0, width: 80, height: 80),
            visibleFrame: CGRect(x: 0, y: 0, width: 1_000, height: 800),
            fallbackAnimation: .runningRight,
            deltaTime: PetLaunchMotion.frameInterval
        )

        XCTAssertTrue(step.shouldStop)
    }

    func testPetLaunchMotionClampsAtVisibleFrameEdge() {
        let step = PetLaunchMotion.step(
            origin: CGPoint(x: 395, y: 100),
            velocity: CGVector(dx: 900, dy: 0),
            movingFrame: CGRect(x: 0, y: 0, width: 100, height: 80),
            visibleFrame: CGRect(x: 0, y: 0, width: 500, height: 500),
            fallbackAnimation: .runningRight,
            deltaTime: PetLaunchMotion.frameInterval
        )

        XCTAssertEqual(step.origin.x, 400)
        XCTAssertEqual(step.velocity.dx, 0)
        XCTAssertTrue(step.shouldStop)
    }

    func testPetLaunchMotionClampsVisibleSpriteNotWholePanel() {
        let leftStep = PetLaunchMotion.step(
            origin: CGPoint(x: -170, y: 100),
            velocity: CGVector(dx: -900, dy: 0),
            movingFrame: CGRect(x: 180, y: 100, width: 80, height: 80),
            visibleFrame: CGRect(x: 0, y: 0, width: 500, height: 500),
            fallbackAnimation: .runningLeft,
            deltaTime: PetLaunchMotion.frameInterval
        )
        let topStep = PetLaunchMotion.step(
            origin: CGPoint(x: 100, y: 345),
            velocity: CGVector(dx: 0, dy: 900),
            movingFrame: CGRect(x: 180, y: 0, width: 80, height: 150),
            visibleFrame: CGRect(x: 0, y: 0, width: 500, height: 500),
            fallbackAnimation: .runningRight,
            deltaTime: PetLaunchMotion.frameInterval
        )

        XCTAssertEqual(leftStep.origin.x, -180)
        XCTAssertEqual(leftStep.velocity.dx, 0)
        XCTAssertEqual(topStep.origin.y, 350)
        XCTAssertEqual(topStep.velocity.dy, 0)
    }

    private func makeTemporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func writePNG(width: Int, height: Int, to url: URL) throws {
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard
            let context = CGContext(
                data: nil,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: width * 4,
                space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ),
            let image = context.makeImage(),
            let destination = CGImageDestinationCreateWithURL(
                url as CFURL,
                UTType.png.identifier as CFString,
                1,
                nil
            )
        else {
            XCTFail("Could not create test PNG")
            return
        }

        CGImageDestinationAddImage(destination, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(destination))
    }

    private func makeAlphaTestImage(width: Int, height: Int, alphas: [UInt8]) throws -> CGImage {
        XCTAssertEqual(alphas.count, width * height)
        let bytesPerPixel = 4
        let bytesPerRow = width * bytesPerPixel
        var pixels = [UInt8](repeating: 0, count: bytesPerRow * height)
        for (index, alpha) in alphas.enumerated() {
            pixels[index * bytesPerPixel] = alpha
            pixels[index * bytesPerPixel + 1] = alpha
            pixels[index * bytesPerPixel + 2] = alpha
            pixels[index * bytesPerPixel + 3] = alpha
        }

        guard
            let provider = CGDataProvider(data: Data(pixels) as CFData),
            let image = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: bytesPerRow,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGBitmapInfo(
                    rawValue: CGImageAlphaInfo.premultipliedLast.rawValue
                        | CGBitmapInfo.byteOrder32Big.rawValue
                ),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
            )
        else {
            throw NSError(domain: "OpenPetsTests", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not create alpha test image"])
        }

        return image
    }

    private func makePetBundle(id: String, at directory: URL) throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data(
            """
            {
              "id": "\(id)",
              "displayName": "Test Pet",
              "description": "Installed test pet.",
              "spritesheetPath": "spritesheet.png"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("pet.json"))
        try writePNG(width: 1536, height: 1872, to: directory.appendingPathComponent("spritesheet.png"))
    }

    private func zipDirectory(_ directory: URL, to archiveURL: URL) throws {
        _ = try runProcess(
            "/usr/bin/ditto",
            arguments: ["-c", "-k", directory.path, archiveURL.path],
            workingDirectory: directory.deletingLastPathComponent()
        )
    }

    private func runProcess(_ executable: String, arguments: [String], workingDirectory: URL? = nil) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.currentDirectoryURL = workingDirectory
        let output = Pipe()
        let error = Pipe()
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        let outputData = output.fileHandleForReading.readDataToEndOfFile()
        let errorData = error.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            throw NSError(
                domain: "OpenPetsTests",
                code: Int(process.terminationStatus),
                userInfo: [NSLocalizedDescriptionKey: String(data: errorData, encoding: .utf8) ?? "process failed"]
            )
        }
        return String(data: outputData, encoding: .utf8) ?? ""
    }

    private func makeExecutable(named name: String, in directory: URL) throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let executableURL = directory.appendingPathComponent(name)
        try Data("#!/bin/sh\n".utf8).write(to: executableURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executableURL.path)
        return executableURL
    }

    private func writeClaudeConfig(to url: URL, mcpURL: String) throws {
        let object: [String: Any] = [
            "theme": "light",
            "mcpServers": [
                "openpets": [
                    "type": "http",
                    "url": mcpURL
                ]
            ]
        ]
        let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }

    private func schemaProperty(toolName: String, propertyName: String) throws -> [String: Value] {
        let tool = try XCTUnwrap(openPetsTools().first { $0.name == toolName })
        let inputSchema = try XCTUnwrap(tool.inputSchema.objectValue)
        let properties = try XCTUnwrap(inputSchema["properties"]?.objectValue)
        return try XCTUnwrap(properties[propertyName]?.objectValue)
    }

    private func schemaRequired(toolName: String) throws -> [String] {
        let tool = try XCTUnwrap(openPetsTools().first { $0.name == toolName })
        let inputSchema = try XCTUnwrap(tool.inputSchema.objectValue)
        return inputSchema["required"]?.arrayValue?.compactMap(\.stringValue) ?? []
    }

    @MainActor
    private func mouseEvent(type: NSEvent.EventType, location: CGPoint, window: NSWindow) -> NSEvent? {
        NSEvent.mouseEvent(
            with: type,
            location: location,
            modifierFlags: [],
            timestamp: 0,
            windowNumber: window.windowNumber,
            context: nil,
            eventNumber: 0,
            clickCount: 1,
            pressure: 1
        )
    }
}

private struct FakePortChecker: OpenPetsPortChecking {
    var availablePorts: Set<Int>

    init(availablePorts: Set<Int>) {
        self.availablePorts = availablePorts
    }

    func isPortAvailable(host _: String, port: Int) -> Bool {
        availablePorts.contains(port)
    }
}

private final class FakeProcessRunner: OpenPetsProcessRunning, @unchecked Sendable {
    var responses: [String: OpenPetsProcessResult]
    private(set) var recordedInvocations: [String] = []

    init(responses: [String: OpenPetsProcessResult]) {
        self.responses = responses
    }

    static func key(_ executablePath: String, _ arguments: [String]) -> String {
        ([executablePath] + arguments).joined(separator: "\u{1f}")
    }

    func run(executableURL: URL, arguments: [String]) throws -> OpenPetsProcessResult {
        let key = Self.key(executableURL.path, arguments)
        recordedInvocations.append(key)
        return responses[key] ?? .failure("missing fake response")
    }
}

private final class FakeWorkspaceOpen: @unchecked Sendable {
    private(set) var openedURLs: [URL] = []
    private(set) var activationValues: [Bool] = []
    private(set) var completions: [OpenPetsActionURLOpener.Completion] = []

    func open(
        url: URL,
        configuration: NSWorkspace.OpenConfiguration,
        completion: @escaping OpenPetsActionURLOpener.Completion
    ) {
        openedURLs.append(url)
        activationValues.append(configuration.activates)
        completions.append(completion)
    }
}

private extension OpenPetsProcessResult {
    static func success(_ output: String) -> OpenPetsProcessResult {
        OpenPetsProcessResult(terminationStatus: 0, standardOutput: output, standardError: "")
    }

    static func failure(_ error: String) -> OpenPetsProcessResult {
        OpenPetsProcessResult(terminationStatus: 1, standardOutput: "", standardError: error)
    }
}
