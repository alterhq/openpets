import CoreGraphics
import Foundation
import ImageIO
@testable import OpenPetsCore
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
            socketPath: "/tmp/openpets-test.sock"
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
        XCTAssertTrue(FileManager.default.fileExists(atPath: url.path))
    }

    func testPetCommandRoundTripCoding() throws {
        let commands: [PetCommand] = [
            .setMessage(text: "hello", ttlSeconds: 2, priority: 3),
            .setStatus(kind: "review", message: "Reviewing changes", ttlSeconds: 4),
            .playAnimation(name: .waving, loop: false, ttlSeconds: 1),
            .clearMessage,
            .ping,
            .shutdown
        ]

        for command in commands {
            let data = try JSONEncoder().encode(command)
            let decoded = try JSONDecoder().decode(PetCommand.self, from: data)
            XCTAssertEqual(decoded, command)
        }
    }

    func testUnixSocketClientServerFraming() throws {
        let socketPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-\(UUID().uuidString).sock")
            .path
        let server = OpenPetsServer(socketPath: socketPath) { command in
            switch command {
            case .ping:
                PetResponse(ok: true, message: "pong")
            case .setMessage(let text, _, _):
                PetResponse(ok: true, message: text)
            default:
                PetResponse(ok: false, message: "unexpected")
            }
        }
        try server.start()
        defer { server.stop() }

        let client = OpenPetsClient(socketPath: socketPath)
        XCTAssertEqual(try client.send(.ping), PetResponse(ok: true, message: "pong"))
        XCTAssertEqual(
            try client.send(.setMessage(text: "hello", ttlSeconds: nil, priority: nil)),
            PetResponse(ok: true, message: "hello")
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
}
