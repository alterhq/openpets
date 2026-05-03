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
                xURLCallback: "openpets://review?id=123",
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

    func testPetNotificationUsesXURLCallbackCodingKey() throws {
        let notification = PetNotification(
            title: "Reply needed",
            text: "A user asked a follow-up.",
            status: "reply",
            xURLCallback: "openpets://reply?id=42",
            buttonLabel: "Reply",
            ttlSeconds: 10
        )

        let data = try JSONEncoder().encode(notification)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(json["x-url-callback"] as? String, "openpets://reply?id=42")

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

    func testPositionStorePersistsPerPet() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let storeURL = directory.appendingPathComponent("positions.json")
        let store = PetPositionStore(url: storeURL)

        try store.savePosition(CGPoint(x: 12, y: 34), forPetID: "starcorn")

        let reloaded = PetPositionStore(url: storeURL)
        XCTAssertEqual(reloaded.loadPosition(forPetID: "starcorn"), CGPoint(x: 12, y: 34))
        XCTAssertNil(reloaded.loadPosition(forPetID: "other"))
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
}
