import AppKit
import Foundation
import Logging
import MCP
import OpenPetsKit
@testable import OpenPetsMenuBar
import XCTest

final class OpenPetsTests: XCTestCase {
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

    func testReleasePackageIncludesMenuBarResourceBundle() throws {
        let rootURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let scriptURL = rootURL.appendingPathComponent("scripts/package-release.sh")
        let script = try String(contentsOf: scriptURL, encoding: .utf8)

        XCTAssertTrue(script.contains("OpenPetsKit_OpenPetsKit.bundle"))
        XCTAssertTrue(script.contains("$APP_BUNDLE/Contents/Resources/OpenPetsKit_OpenPetsKit.bundle/pet.json"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle"))
        XCTAssertTrue(script.contains("$APP_BUNDLE/Contents/Resources/OpenPets_OpenPetsMenuBar.bundle/codex.png"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle/codex.png"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle/claude.png"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle/pi.png"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle/opencode.png"))
        XCTAssertTrue(script.contains("OpenPets_OpenPetsMenuBar.bundle/zed.png"))
    }

    @MainActor
    func testPetContextMenuMatchesStatusMenuTopLevelItems() {
        let controller = OpenPetsMenuBarController()
        let statusMenu = controller.makeStatusItemMenu()
        let petContextMenu = controller.makePetContextMenu()

        XCTAssertEqual(menuItemTitles(statusMenu), menuItemTitles(petContextMenu))
        XCTAssertNotNil(petContextMenu.items.first { $0.title.hasPrefix("Active Pet:") }?.submenu)
    }

    @MainActor
    func testMenusIncludeCallMyPetNearWakePet() {
        let controller = OpenPetsMenuBarController()
        let menu = controller.makeStatusItemMenu()
        let titles = menuItemTitles(menu)

        XCTAssertEqual(
            titles.firstIndex(of: "Call My Pet"),
            titles.firstIndex(of: "Wake Pet").map { $0 + 1 }
        )
        XCTAssertEqual(
            titles.firstIndex(of: "Clear All Notifications"),
            titles.firstIndex(of: "Call My Pet").map { $0 + 1 }
        )
        XCTAssertEqual(
            titles.firstIndex { $0.hasPrefix("Active Pet:") },
            titles.firstIndex(of: "Clear All Notifications").map { $0 + 1 }
        )
    }

    @MainActor
    func testMenusIncludeGallerySettingsAssistantConnectionAndVersion() throws {
        let controller = OpenPetsMenuBarController()
        let menu = controller.makeStatusItemMenu()
        let titles = menuItemTitles(menu)

        XCTAssertTrue(titles.contains("Install Pets..."))
        XCTAssertTrue(titles.contains("Plugins"))
        XCTAssertTrue(titles.contains("Connect Assistants..."))
        XCTAssertFalse(titles.contains("Set Up AI Assistants..."))
        XCTAssertFalse(titles.contains("Install CLI"))

        let settingsItem = try XCTUnwrap(menu.items.first { $0.title == "Settings" })
        let settingsMenu = try XCTUnwrap(settingsItem.submenu)
        XCTAssertEqual(
            menuItemTitles(settingsMenu),
            [
                "Server Status: Stopped",
                "Start MCP Server",
                "Copy MCP URL",
                "<separator>",
                "Open Config Folder",
                "Install CLI Tool"
            ]
        )

        let versionItem = try XCTUnwrap(menu.items.first { $0.title.hasPrefix("Version ") })
        XCTAssertFalse(versionItem.isEnabled)
    }

    @MainActor
    func testMenuIncludesPluginToggleSubmenu() throws {
        try withTemporaryXDGConfigHome {
            let controller = OpenPetsMenuBarController()
            let menu = controller.makeStatusItemMenu()

            let pluginsItem = try XCTUnwrap(menu.items.first { $0.title == "Plugins" })
            let submenu = try XCTUnwrap(pluginsItem.submenu)

            XCTAssertEqual(submenu.items.map(\.title), ["Battery", "Claude Code", "Codex Usage"])
            XCTAssertTrue(submenu.items.allSatisfy { $0.action != nil })
            XCTAssertEqual(submenu.items.first { $0.title == "Battery" }?.representedObject as? String, OpenPetsBatterySurfacePlugin.pluginID)
            XCTAssertEqual(
                submenu.items.first { $0.title == "Claude Code" }?.representedObject as? String,
                OpenPetsClaudeCodeSurfacePlugin.pluginID
            )
            XCTAssertEqual(
                submenu.items.first { $0.title == "Codex Usage" }?.representedObject as? String,
                OpenPetsCodexUsageSurfacePlugin.pluginID
            )
            XCTAssertEqual(submenu.items.first { $0.title == "Battery" }?.state, .on)
            XCTAssertEqual(submenu.items.first { $0.title == "Claude Code" }?.state, .off)
            XCTAssertEqual(submenu.items.first { $0.title == "Codex Usage" }?.state, .off)
        }
    }

    func testPluginTogglePersistsNonDefaultPluginOptIn() throws {
        let batteryPluginID = "openpets.plugin.battery"
        let claudeCodePluginID = "openpets.plugin.claude-code"
        var configuration = OpenPetsConfiguration()

        XCTAssertTrue(configuration.isPluginEnabled(batteryPluginID))
        XCTAssertFalse(configuration.isPluginEnabled(claudeCodePluginID))

        configuration.setPlugin(claudeCodePluginID, enabled: true)

        XCTAssertTrue(configuration.isPluginEnabled(claudeCodePluginID))
        XCTAssertEqual(configuration.enabledPluginIDs, [claudeCodePluginID])
        XCTAssertEqual(configuration.disabledPluginIDs, [])

        configuration.setPlugin(claudeCodePluginID, enabled: false)

        XCTAssertFalse(configuration.isPluginEnabled(claudeCodePluginID))
        XCTAssertEqual(configuration.enabledPluginIDs, [])
        XCTAssertEqual(configuration.disabledPluginIDs, [claudeCodePluginID])

        configuration.setPlugin(batteryPluginID, enabled: false)

        XCTAssertFalse(configuration.isPluginEnabled(batteryPluginID))
        XCTAssertEqual(configuration.enabledPluginIDs, [])
        XCTAssertEqual(configuration.disabledPluginIDs, [batteryPluginID, claudeCodePluginID])
    }

    @MainActor
    func testSurfaceContextMenuShowsPositionDetailsAndPluginActions() throws {
        try withTemporaryXDGConfigHome {
            let controller = OpenPetsMenuBarController()
            let update = OpenPetsSurfaceUpdate(
                surfaceID: "battery.badge",
                slotPreference: [.hotspotTopTrailing, .hotspotRight],
                icon: OpenPetsSurfaceIcons.battery75,
                value: "68%",
                label: "Battery",
                detail: OpenPetsSurfaceDetailData(title: "Battery", rows: [
                    OpenPetsSurfaceDetailRow(label: "Charge", value: "68%")
                ])
            )
            controller.setSurfaceUpdates([update], forPluginID: OpenPetsBatterySurfacePlugin.pluginID)

            let menu = try XCTUnwrap(controller.makeSurfaceContextMenu(for: OpenPetsResolvedSurface(
                update: update,
                placement: .placed(.hotspotTopTrailing)
            )))

            XCTAssertEqual(menu.items.first?.title, "Position: Top Trailing")
            XCTAssertFalse(menu.items.first?.isEnabled ?? true)

            let moveItem = try XCTUnwrap(menu.items.first { $0.title == "Move to" })
            let moveMenu = try XCTUnwrap(moveItem.submenu)
            XCTAssertEqual(
                moveMenu.items.map(\.title),
                [
                    "Top Trailing",
                    "Top Leading",
                    "Right",
                    "Bottom Trailing",
                    "Bottom Leading",
                    "Left",
                    "Below Leading",
                    "Below Trailing"
                ]
            )
            XCTAssertEqual(moveMenu.items.first { $0.title == "Top Trailing" }?.state, .on)
            XCTAssertEqual(moveMenu.items.first { $0.title == "Right" }?.state, .off)

            let detailItem = try XCTUnwrap(menu.items.first { $0.title == "Open Details" })
            XCTAssertTrue(detailItem.isEnabled)
            XCTAssertEqual(detailItem.representedObject as? String, "battery.badge")
            XCTAssertNotNil(detailItem.action)

            let disableItem = try XCTUnwrap(menu.items.first { $0.title == "Disable Battery" })
            XCTAssertEqual(disableItem.representedObject as? String, OpenPetsBatterySurfacePlugin.pluginID)
            XCTAssertNotNil(disableItem.action)

            let belowMenu = try XCTUnwrap(controller.makeSurfaceContextMenu(for: OpenPetsResolvedSurface(
                update: update,
                placement: .placed(.hotspotBelowLeading)
            )))
            XCTAssertEqual(belowMenu.items.first?.title, "Position: Below Leading")
        }
    }

    @MainActor
    func testSurfaceContextMenuDisablesOpenDetailsWhenSurfaceHasNoDetail() throws {
        let controller = OpenPetsMenuBarController()
        let update = OpenPetsSurfaceUpdate(
            surfaceID: "battery.badge",
            icon: OpenPetsSurfaceIcons.battery75,
            value: "68%",
            label: "Battery"
        )

        let menu = try XCTUnwrap(controller.makeSurfaceContextMenu(for: OpenPetsResolvedSurface(
            update: update,
            placement: .placed(.hotspotTopTrailing)
        )))

        let detailItem = try XCTUnwrap(menu.items.first { $0.title == "Open Details" })
        XCTAssertFalse(detailItem.isEnabled)
    }

    @MainActor
    func testSelectingSurfaceSlotPersistsOverrideAndReordersPreferences() throws {
        try withTemporaryXDGConfigHome {
            let controller = OpenPetsMenuBarController()
            let update = OpenPetsSurfaceUpdate(
                surfaceID: "battery.badge",
                slotPreference: [.hotspotTopTrailing, .hotspotRight],
                icon: OpenPetsSurfaceIcons.battery75,
                value: "68%",
                label: "Battery"
            )
            controller.setSurfaceUpdates([update], forPluginID: OpenPetsBatterySurfacePlugin.pluginID)

            let menu = try XCTUnwrap(controller.makeSurfaceContextMenu(for: OpenPetsResolvedSurface(
                update: update,
                placement: .placed(.hotspotTopTrailing)
            )))
            let rightItem = try XCTUnwrap(menu.items
                .first { $0.title == "Move to" }?
                .submenu?
                .items
                .first { $0.title == "Right" })
            let action = try XCTUnwrap(rightItem.action)

            XCTAssertTrue(NSApplication.shared.sendAction(action, to: rightItem.target, from: rightItem))

            let reloaded = try OpenPetsConfiguration.load()
            XCTAssertEqual(reloaded.surfaceSlotOverridesByID["battery.badge"], .hotspotRight)

            controller.reloadConfiguration()
            XCTAssertEqual(
                controller.applyingSurfaceSlotOverride(to: update).slotPreference,
                [.hotspotRight, .hotspotTopTrailing]
            )
        }
    }

    @MainActor
    func testSurfaceRevealTargetsOnlyEnabledPluginSurfaceIDs() {
        let controller = OpenPetsMenuBarController()
        let updates = [
            OpenPetsSurfaceUpdate(
                surfaceID: "claude.5h",
                icon: OpenPetsSurfaceIcons.sparkles,
                value: "42%"
            ),
            OpenPetsSurfaceUpdate(
                surfaceID: "claude.7d",
                icon: OpenPetsSurfaceIcons.clock,
                value: "18%"
            )
        ]

        XCTAssertEqual(controller.surfaceRevealTargetIDs(for: updates), ["claude.5h", "claude.7d"])
        XCTAssertEqual(controller.surfaceRevealTargetIDs(for: []), [])
    }

    @MainActor
    func testMenuIncludesScaleSubmenu() throws {
        let controller = OpenPetsMenuBarController()
        let menu = controller.makeStatusItemMenu()

        let scaleItem = try XCTUnwrap(menu.items.first { $0.title.hasPrefix("Scale:") })
        let submenu = try XCTUnwrap(scaleItem.submenu)

        XCTAssertEqual(
            submenu.items.map(\.title),
            ["0.42x", "0.57x", "0.72x", "0.87x", "1.02x", "1.17x", "1.32x", "1.47x", "1.62x", "1.77x", "1.92x"]
        )
        XCTAssertEqual(
            submenu.items.compactMap { ($0.representedObject as? NSNumber)?.doubleValue },
            [0.42, 0.57, 0.72, 0.87, 1.02, 1.17, 1.32, 1.47, 1.62, 1.77, 1.92]
        )
        XCTAssertTrue(submenu.items.allSatisfy { $0.action != nil })
    }

    @MainActor
    func testSelectingScaleSavesScaleForActivePet() throws {
        try withTemporaryXDGConfigHome {
            try OpenPetsConfiguration(
                display: OpenPetsDisplayConfiguration(scale: 0.75),
                activePetID: "active-pet",
                petScalesByID: ["other-pet": 1.25]
            ).save()

            let controller = OpenPetsMenuBarController()
            let menu = controller.makeStatusItemMenu()
            let scaleItem = try XCTUnwrap(menu.items.first { $0.title == "Scale: 0.75x" })
            let option = try XCTUnwrap(scaleItem.submenu?.items.first { $0.title == "1.47x" })
            let action = try XCTUnwrap(option.action)

            XCTAssertTrue(NSApplication.shared.sendAction(action, to: option.target, from: option))

            let reloaded = try OpenPetsConfiguration.load()
            XCTAssertEqual(reloaded.petScalesByID["active-pet"], 1.47)
            XCTAssertEqual(reloaded.petScalesByID["other-pet"], 1.25)
            XCTAssertEqual(reloaded.display.scale, 0.75)
        }
    }

    @MainActor
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
        XCTAssertTrue(try XCTUnwrap(descriptions["stop_pet_animation"]).contains("return the visible pet to idle"))
        XCTAssertTrue(try XCTUnwrap(descriptions["stop_pet_animation"]).contains("without stopping, hiding, or clearing pet messages"))
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

    func testMCPStopAnimationSchemaHasNoRequiredArguments() throws {
        let required = try schemaRequired(toolName: "stop_pet_animation")

        XCTAssertTrue(required.isEmpty)
    }

    func testBatterySurfacePluginShowsCloudSurfaceForNormalBattery() {
        let updates = OpenPetsBatterySurfacePlugin.surfaceUpdates(for: OpenPetsBatterySnapshot(
            percent: 68,
            isCharging: false,
            isPresent: true,
            timeRemainingMinutes: 185
        ))

        XCTAssertEqual(updates.map(\.surfaceID), ["battery.badge"])
        XCTAssertEqual(updates.first?.slotPreference, [.hotspotTopTrailing, .hotspotRight])
        XCTAssertEqual(updates.first?.icon, OpenPetsSurfaceIcons.battery75)
        XCTAssertEqual(updates.first?.value, "68%")
        XCTAssertEqual(updates.first?.label, "Battery")
        XCTAssertEqual(updates.first?.tone, .normal)
        XCTAssertEqual(updates.first?.detail?.title, "Battery")
        XCTAssertEqual(updates.first?.detail?.rows.map(\.label), ["Charge", "State", "Remaining"])
        XCTAssertEqual(updates.first?.detail?.rows.first { $0.label == "State" }?.value, "Unplugged")
        XCTAssertEqual(
            updates.first?.detail?.actionURL,
            "x-apple.systempreferences:com.apple.Battery-Settings.extension"
        )
        XCTAssertEqual(updates.first?.detail?.actionLabel, "Settings")
        XCTAssertEqual(updates.first?.detail?.ttlSeconds, 8)
    }

    func testBatterySurfacePluginKeepsLowBatteryAsCriticalCloudSurface() {
        let updates = OpenPetsBatterySurfacePlugin.surfaceUpdates(for: OpenPetsBatterySnapshot(
            percent: 9,
            isCharging: false,
            isPresent: true,
            timeRemainingMinutes: 22
        ))

        XCTAssertEqual(updates.map(\.surfaceID), ["battery.badge"])
        let badge = try! XCTUnwrap(updates.first)
        XCTAssertEqual(badge.priority, 90)
        XCTAssertEqual(badge.icon, OpenPetsSurfaceIcons.battery25)
        XCTAssertEqual(badge.value, "9%")
        XCTAssertEqual(badge.tone, .critical)

        XCTAssertEqual(OpenPetsBatterySurfacePlugin.reactionUpdates(for: OpenPetsBatterySnapshot(
            percent: 9,
            isCharging: false,
            isPresent: true,
            timeRemainingMinutes: 22
        )), [
            OpenPetsPetReactionUpdate(reactionID: "battery.low-energy", kind: .lowEnergy, priority: 90)
        ])
    }

    func testBatterySurfacePluginKeepsChargingBatteryAsSuccessCloudSurfaceWithoutReaction() {
        let updates = OpenPetsBatterySurfacePlugin.surfaceUpdates(for: OpenPetsBatterySnapshot(
            percent: 82,
            isCharging: true,
            isPlugged: true,
            isPresent: true,
            timeRemainingMinutes: nil,
            timeToFullChargeMinutes: 48
        ))

        XCTAssertEqual(updates.map(\.surfaceID), ["battery.badge"])
        let badge = try! XCTUnwrap(updates.first { $0.surfaceID == "battery.badge" })
        XCTAssertEqual(badge.icon, OpenPetsSurfaceIcons.batteryCharging)
        XCTAssertEqual(badge.value, "82%")
        XCTAssertEqual(badge.tone, .success)
        XCTAssertEqual(badge.detail?.rows.map(\.label), ["Charge", "State", "Full"])
        XCTAssertEqual(badge.detail?.rows.first { $0.label == "State" }?.value, "Plugged")
        XCTAssertEqual(badge.detail?.rows.first { $0.label == "Full" }?.value, "48m")

        XCTAssertTrue(OpenPetsBatterySurfacePlugin.reactionUpdates(for: OpenPetsBatterySnapshot(
            percent: 82,
            isCharging: true,
            isPlugged: true,
            isPresent: true,
            timeRemainingMinutes: nil,
            timeToFullChargeMinutes: 48
        )).isEmpty)
    }

    func testBatterySurfacePluginDoesNotEmitChargingReactionBelowEightyPercent() {
        XCTAssertTrue(OpenPetsBatterySurfacePlugin.reactionUpdates(for: OpenPetsBatterySnapshot(
            percent: 64,
            isCharging: true,
            isPlugged: true,
            isPresent: true,
            timeRemainingMinutes: nil,
            timeToFullChargeMinutes: 42
        )).isEmpty)
    }

    func testBatterySurfacePluginReturnsNoSurfacesWhenBatteryMissing() {
        XCTAssertTrue(OpenPetsBatterySurfacePlugin.surfaceUpdates(for: nil).isEmpty)
        XCTAssertTrue(OpenPetsBatterySurfacePlugin.reactionUpdates(for: nil).isEmpty)
        XCTAssertTrue(OpenPetsBatterySurfacePlugin.surfaceUpdates(for: OpenPetsBatterySnapshot(
            percent: 50,
            isCharging: false,
            isPresent: false,
            timeRemainingMinutes: nil
        )).isEmpty)
        XCTAssertTrue(OpenPetsBatterySurfacePlugin.reactionUpdates(for: OpenPetsBatterySnapshot(
            percent: 50,
            isCharging: false,
            isPresent: false,
            timeRemainingMinutes: nil
        )).isEmpty)
    }

    func testClaudeCodeQuotaReaderParsesOAuthUsagePayload() throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let sevenDayReset = ISO8601DateFormatter().string(from: now.addingTimeInterval(3 * 24 * 60 * 60))
        let data = Data(
            """
            {
              "five_hour": {
                "utilization": 42.8,
                "resets_at": "2027-01-15T09:30:00.528743+00:00"
              },
              "seven_day": {
                "utilization": 18.1,
                "resets_at": "\(sevenDayReset)"
              },
              "seven_day_opus": null,
              "seven_day_sonnet": null,
              "extra_usage": {
                "is_enabled": false
              }
            }
            """.utf8
        )

        let snapshot = try XCTUnwrap(OpenPetsClaudeCodeQuotaReader.snapshot(fromOAuthUsageData: data, now: now))

        XCTAssertEqual(snapshot.fiveHour.usedPercentage, 42)
        XCTAssertEqual(snapshot.fiveHour.durationMinutes, 300)
        XCTAssertEqual(
            snapshot.fiveHour.resetDate.timeIntervalSince1970,
            1_800_005_400.528743,
            accuracy: 0.001
        )
        XCTAssertEqual(snapshot.sevenDay.usedPercentage, 18)
        XCTAssertEqual(snapshot.sevenDay.durationMinutes, 10_080)
        XCTAssertEqual(snapshot.sevenDay.resetDate, now.addingTimeInterval(3 * 24 * 60 * 60))
    }

    func testClaudeCodeQuotaReaderLoadsCredentialsFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let credentialsURL = directory.appendingPathComponent(".credentials.json")
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        try Data(
            """
            {
              "claudeAiOauth": {
                "accessToken": "test-access-token",
                "refreshToken": "test-refresh-token",
                "expiresAt": \((now.timeIntervalSince1970 + 3600) * 1000)
              }
            }
            """.utf8
        ).write(to: credentialsURL)

        let credentials = OpenPetsClaudeCodeQuotaReader(
            credentialsURL: credentialsURL,
            processRunner: FakeProcessRunner(responses: [:]),
            environment: [:]
        ).credentials(now: now)

        XCTAssertEqual(
            credentials,
            OpenPetsClaudeCodeOAuthCredentials(
                accessToken: "test-access-token",
                expiresAt: now.addingTimeInterval(3600)
            )
        )
    }

    func testClaudeCodeQuotaReaderUsesEnvironmentOAuthToken() {
        let credentials = OpenPetsClaudeCodeQuotaReader(
            credentialsURL: URL(fileURLWithPath: "/tmp/missing-credentials.json"),
            processRunner: FakeProcessRunner(responses: [:]),
            environment: ["CLAUDE_CODE_OAUTH_TOKEN": "env-access-token"]
        ).credentials()

        XCTAssertEqual(
            credentials,
            OpenPetsClaudeCodeOAuthCredentials(accessToken: "env-access-token", expiresAt: nil)
        )
    }

    func testClaudeCodeQuotaReaderSendsOAuthUsageHeaders() async throws {
        let requestRecorder = RequestRecorder()
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let fiveHourReset = ISO8601DateFormatter().string(from: now.addingTimeInterval(90 * 60))
        let sevenDayReset = ISO8601DateFormatter().string(from: now.addingTimeInterval(3 * 24 * 60 * 60))
        let data = Data(
            """
            {
              "five_hour": {
                "utilization": 42,
                "resets_at": "\(fiveHourReset)"
              },
              "seven_day": {
                "utilization": 18,
                "resets_at": "\(sevenDayReset)"
              }
            }
            """.utf8
        )
        let usageURL = URL(string: "https://api.anthropic.com/api/oauth/usage")!

        _ = await OpenPetsClaudeCodeQuotaReader(
            credentialsURL: URL(fileURLWithPath: "/tmp/missing-credentials.json"),
            usageURL: usageURL,
            processRunner: FakeProcessRunner(responses: [:]),
            environment: ["CLAUDE_CODE_OAUTH_TOKEN": "env-access-token"],
            userAgent: "claude-code/1.2.3",
            dataLoader: { request in
                requestRecorder.request = request
                return (data, HTTPURLResponse(
                    url: usageURL,
                    statusCode: 200,
                    httpVersion: nil,
                    headerFields: nil
                )!)
            }
        ).snapshot(now: now)

        let request = try XCTUnwrap(requestRecorder.request)
        XCTAssertEqual(request.url, usageURL)
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer env-access-token")
        XCTAssertEqual(request.value(forHTTPHeaderField: "anthropic-beta"), "oauth-2025-04-20")
        XCTAssertEqual(request.value(forHTTPHeaderField: "User-Agent"), "claude-code/1.2.3")
        XCTAssertEqual(request.value(forHTTPHeaderField: "Content-Type"), "application/json")
    }

    func testClaudeCodeQuotaReaderDoesNotRefreshCredentialsOnRateLimit() async {
        let usageURL = URL(string: "https://api.anthropic.com/api/oauth/usage")!
        let runner = FakeProcessRunner(responses: [:])

        let snapshot = await OpenPetsClaudeCodeQuotaReader(
            credentialsURL: URL(fileURLWithPath: "/tmp/missing-credentials.json"),
            usageURL: usageURL,
            processRunner: runner,
            environment: ["CLAUDE_CODE_OAUTH_TOKEN": "env-access-token"],
            dataLoader: { _ in
                (Data(), HTTPURLResponse(
                    url: usageURL,
                    statusCode: 429,
                    httpVersion: nil,
                    headerFields: nil
                )!)
            }
        ).snapshot()

        XCTAssertNil(snapshot)
        XCTAssertEqual(runner.recordedInvocations, [
            FakeProcessRunner.key("/usr/bin/security", ["find-generic-password", "-s", "Claude Code-credentials", "-w"])
        ])
    }

    func testClaudeCodeQuotaReaderRefreshesCredentialsOnUnauthorized() async {
        let usageURL = URL(string: "https://api.anthropic.com/api/oauth/usage")!
        let claudeURL = URL(fileURLWithPath: "/tmp/claude")
        let runner = FakeProcessRunner(responses: [
            FakeProcessRunner.key("/bin/zsh", ["-lc", "command -v claude"]): .success("\(claudeURL.path)\n"),
            FakeProcessRunner.key(claudeURL.path, ["update"]): .success("")
        ])

        let snapshot = await OpenPetsClaudeCodeQuotaReader(
            credentialsURL: URL(fileURLWithPath: "/tmp/missing-credentials.json"),
            usageURL: usageURL,
            processRunner: runner,
            environment: ["CLAUDE_CODE_OAUTH_TOKEN": "env-access-token"],
            dataLoader: { _ in
                (Data(), HTTPURLResponse(
                    url: usageURL,
                    statusCode: 401,
                    httpVersion: nil,
                    headerFields: nil
                )!)
            }
        ).snapshot()

        XCTAssertNil(snapshot)
        XCTAssertTrue(runner.recordedInvocations.contains(FakeProcessRunner.key("/bin/zsh", ["-lc", "command -v claude"])))
        XCTAssertTrue(runner.recordedInvocations.contains(FakeProcessRunner.key(claudeURL.path, ["update"])))
    }

    func testClaudeCodeQuotaReaderDetectsClaudeConfiguration() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let claudeDirectory = directory.appendingPathComponent(".claude", isDirectory: true)
        try FileManager.default.createDirectory(at: claudeDirectory, withIntermediateDirectories: true)

        XCTAssertTrue(OpenPetsClaudeCodeQuotaReader(
            credentialsURL: claudeDirectory.appendingPathComponent(".credentials.json"),
            claudeConfigurationURLs: [claudeDirectory],
            processRunner: FakeProcessRunner(responses: [:]),
            environment: [:]
        ).hasClaudeConfiguration())
    }

    func testClaudeCodeSurfacePluginShowsTwoQuotaCloudSurfaces() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = OpenPetsClaudeCodeQuotaSnapshot(
            fiveHour: OpenPetsClaudeCodeQuotaWindow(
                label: "5h",
                usedPercentage: 42,
                resetDate: now.addingTimeInterval(90 * 60),
                durationMinutes: 300
            ),
            sevenDay: OpenPetsClaudeCodeQuotaWindow(
                label: "7d",
                usedPercentage: 76,
                resetDate: now.addingTimeInterval(3 * 24 * 60 * 60),
                durationMinutes: 10_080
            )
        )

        let updates = OpenPetsClaudeCodeSurfacePlugin.surfaceUpdates(for: snapshot, now: now)

        XCTAssertEqual(updates.map(\.surfaceID), ["claude.5h", "claude.7d"])
        XCTAssertEqual(updates[0].slotPreference, [.hotspotTopLeading, .hotspotLeft])
        XCTAssertEqual(updates[0].icon, OpenPetsSurfaceIcons.quota)
        XCTAssertEqual(updates[0].value, "5h 42%")
        XCTAssertEqual(updates[0].detail?.rows.map(\.label), ["Used", "Reset", "Pace"])
        XCTAssertEqual(updates[0].detail?.rows.first { $0.label == "Reset" }?.value, "1h 30m")
        XCTAssertEqual(updates[0].detail?.rows.first { $0.label == "Pace" }?.value, "28% under target")
        XCTAssertEqual(updates[0].detail?.ttlSeconds, 12)
        XCTAssertEqual(updates[1].slotPreference, [.hotspotBottomLeading, .hotspotLeft])
        XCTAssertEqual(updates[1].value, "7d 76%")
        XCTAssertEqual(updates[1].tone, .warning)
    }

    func testClaudeCodeSurfacePluginShowsSetupCloudWhenConfiguredButMissingQuotaData() {
        let update = OpenPetsClaudeCodeSurfacePlugin.setupSurfaceUpdate()

        XCTAssertEqual(update.surfaceID, "claude.setup")
        XCTAssertEqual(update.icon, OpenPetsSurfaceIcons.info)
        XCTAssertEqual(update.value, "Claude")
        XCTAssertEqual(update.tone, .muted)
        XCTAssertEqual(update.detail?.rows.map(\.label), ["Status", "Source"])
        XCTAssertEqual(
            update.detail?.rows.first { $0.label == "Source" }?.value,
            "Claude Code OAuth"
        )
    }

    func testClaudeCodeSurfacePluginEmitsCriticalReaction() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = OpenPetsClaudeCodeQuotaSnapshot(
            fiveHour: OpenPetsClaudeCodeQuotaWindow(
                label: "5h",
                usedPercentage: 92,
                resetDate: now.addingTimeInterval(20 * 60),
                durationMinutes: 300
            ),
            sevenDay: OpenPetsClaudeCodeQuotaWindow(
                label: "7d",
                usedPercentage: 20,
                resetDate: now.addingTimeInterval(6 * 24 * 60 * 60),
                durationMinutes: 10_080
            )
        )

        XCTAssertEqual(OpenPetsClaudeCodeSurfacePlugin.reactionUpdates(for: snapshot, now: now), [
            OpenPetsPetReactionUpdate(
                reactionID: "claude.quota-critical",
                kind: .alert,
                priority: 80,
                ttlSeconds: 20
            )
        ])
    }

    func testCodexUsageReaderParsesLiveRateLimitPayload() throws {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let data = Data(
            """
            {
              "limits": {
                "primary": {
                  "used_percent": 42.8,
                  "window_minutes": 300,
                  "resets_in_seconds": 5400
                },
                "secondary": {
                  "used_percent": 18,
                  "window_minutes": 10080,
                  "resets_in_seconds": 259200
                },
                "additional": {
                  "used_percent": 8,
                  "window_minutes": 43200,
                  "resets_in_seconds": 864000
                }
              }
            }
            """.utf8
        )

        let snapshot = try XCTUnwrap(OpenPetsCodexUsageReader.snapshot(fromLiveUsageData: data, now: now))

        XCTAssertEqual(snapshot.source, "live")
        XCTAssertEqual(snapshot.primary?.label, "5h")
        XCTAssertEqual(snapshot.primary?.usedPercentage, 42)
        XCTAssertEqual(snapshot.primary?.resetDate, now.addingTimeInterval(5400))
        XCTAssertEqual(snapshot.secondary?.label, "7d")
        XCTAssertEqual(snapshot.additional?.label, "30d")
    }

    func testCodexUsageSurfacePluginShowsUsageCloudSurfaces() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = OpenPetsCodexUsageSnapshot(
            planType: "plus",
            primary: OpenPetsCodexUsageBucket(
                label: "5h",
                usedPercentage: 42,
                windowMinutes: 300,
                resetDate: now.addingTimeInterval(90 * 60),
                kind: "primary"
            ),
            secondary: OpenPetsCodexUsageBucket(
                label: "7d",
                usedPercentage: 76,
                windowMinutes: 10_080,
                resetDate: now.addingTimeInterval(3 * 24 * 60 * 60),
                kind: "secondary"
            ),
            additional: nil,
            observedAt: now,
            source: "live"
        )

        let updates = OpenPetsCodexUsageSurfacePlugin.surfaceUpdates(for: snapshot, now: now)

        XCTAssertEqual(updates.map(\.surfaceID), ["codex.primary", "codex.secondary"])
        XCTAssertEqual(updates[0].slotPreference, [.hotspotTopLeading, .hotspotLeft])
        XCTAssertEqual(updates[0].icon, OpenPetsSurfaceIcons.quota)
        XCTAssertEqual(updates[0].value, "5h 58%")
        XCTAssertEqual(updates[0].detail?.rows.map(\.label), ["Remaining", "Reset", "Pace"])
        XCTAssertEqual(updates[0].detail?.rows.first { $0.label == "Remaining" }?.value, "58%")
        XCTAssertEqual(updates[0].detail?.rows.first { $0.label == "Pace" }?.value, "28% under target")
        XCTAssertNil(updates[0].detail?.rows.first { $0.label == "Used" })
        XCTAssertNil(updates[0].detail?.rows.first { $0.label == "Source" })
        XCTAssertNil(updates[0].detail?.rows.first { $0.label == "Plan" })
        let resetValue = try! XCTUnwrap(updates[0].detail?.rows.first { $0.label == "Reset" }?.value)
        XCTAssertTrue(resetValue.contains("in 1h 30m"))
        XCTAssertTrue(resetValue.contains(":"))
        XCTAssertFalse(resetValue.contains("2027"))
        XCTAssertEqual(updates[0].detail?.ttlSeconds, 12)
        XCTAssertEqual(updates[1].slotPreference, [.hotspotBottomLeading, .hotspotLeft])
        XCTAssertEqual(updates[1].value, "7d 24%")
        XCTAssertEqual(updates[1].tone, .warning)
        XCTAssertEqual(updates[1].detail?.rows.first { $0.label == "Pace" }?.value, "19% over target")
    }

    func testCodexUsageSurfacePluginShowsSetupCloudWhenConfiguredButMissingUsageData() {
        let update = OpenPetsCodexUsageSurfacePlugin.setupSurfaceUpdate()

        XCTAssertEqual(update.surfaceID, "codex.usage.setup")
        XCTAssertEqual(update.icon, OpenPetsSurfaceIcons.info)
        XCTAssertEqual(update.value, "Codex")
        XCTAssertEqual(update.tone, .muted)
        XCTAssertEqual(update.detail?.rows.map(\.label), ["Status", "Source"])
    }

    func testCodexUsageSurfacePluginEmitsCriticalReaction() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let snapshot = OpenPetsCodexUsageSnapshot(
            planType: nil,
            primary: OpenPetsCodexUsageBucket(
                label: "5h",
                usedPercentage: 92,
                windowMinutes: 300,
                resetDate: now.addingTimeInterval(20 * 60),
                kind: "primary"
            ),
            secondary: OpenPetsCodexUsageBucket(
                label: "7d",
                usedPercentage: 20,
                windowMinutes: 10_080,
                resetDate: now.addingTimeInterval(6 * 24 * 60 * 60),
                kind: "secondary"
            ),
            additional: nil,
            observedAt: now,
            source: "live"
        )

        XCTAssertEqual(OpenPetsCodexUsageSurfacePlugin.reactionUpdates(for: snapshot), [
            OpenPetsPetReactionUpdate(
                reactionID: "codex.usage-critical",
                kind: .alert,
                priority: 75,
                ttlSeconds: 20
            )
        ])
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

    func testAgentDetectorFindsConfiguredPiMCP() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let piURL = try makeExecutable(named: "pi", in: directory)
        let piConfigURL = directory.appendingPathComponent(".pi/agent/mcp.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writePiMCPConfig(to: piConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            piMCPConfigurationURL: piConfigURL
        ).detect(.pi, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertEqual(detection.executableURL?.path, piURL.path)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsDifferentConfiguredPiURL() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "pi", in: directory)
        let piConfigURL = directory.appendingPathComponent(".pi/agent/mcp.json")
        try writePiMCPConfig(to: piConfigURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            piMCPConfigurationURL: piConfigURL
        ).detect(.pi, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorFindsConfiguredOpenCodeMCP() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let openCodeURL = try makeExecutable(named: "opencode", in: directory)
        let openCodeConfigURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeOpenCodeConfig(to: openCodeConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeConfigURL
        ).detect(.openCode, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertEqual(detection.executableURL?.path, openCodeURL.path)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsDifferentConfiguredOpenCodeURL() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "opencode", in: directory)
        let openCodeConfigURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        try writeOpenCodeConfig(to: openCodeConfigURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeConfigURL
        ).detect(.openCode, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsDisabledOpenCodeMCPAsUpdateNeeded() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "opencode", in: directory)
        let openCodeConfigURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeOpenCodeConfig(to: openCodeConfigURL, mcpURL: mcpURL, enabled: false)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeConfigURL
        ).detect(.openCode, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReadsOpenCodeJSONCConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "opencode", in: directory)
        let openCodeConfigURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeOpenCodeJSONCConfig(to: openCodeConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeConfigURL
        ).detect(.openCode, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorFindsOpenCodeJSONCConfigFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "opencode", in: directory)
        let openCodeJSONURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let openCodeJSONCURL = directory.appendingPathComponent(".config/opencode/opencode.jsonc")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeOpenCodeJSONCConfig(to: openCodeJSONCURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeJSONURL
        ).detect(.openCode, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertFalse(FileManager.default.fileExists(atPath: openCodeJSONURL.path))
    }

    func testAgentDetectorPrefersOpenCodeJSONCConfigFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "opencode", in: directory)
        let openCodeJSONURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let openCodeJSONCURL = directory.appendingPathComponent(".config/opencode/opencode.jsonc")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeOpenCodeConfig(to: openCodeJSONURL, mcpURL: mcpURL, enabled: false)
        try writeOpenCodeJSONCConfig(to: openCodeJSONCURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            openCodeConfigurationURL: openCodeJSONURL
        ).detect(.openCode, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
    }

    func testAgentDetectorFindsConfiguredZedMCP() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let zedURL = try makeExecutable(named: "zed", in: directory)
        let zedConfigURL = directory.appendingPathComponent(".config/zed/settings.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeZedSettings(to: zedConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            zedConfigurationURL: zedConfigURL
        ).detect(.zed, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertEqual(detection.executableURL?.path, zedURL.path)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsDifferentConfiguredZedURL() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "zed", in: directory)
        let zedConfigURL = directory.appendingPathComponent(".config/zed/settings.json")
        try writeZedSettings(to: zedConfigURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            zedConfigurationURL: zedConfigURL
        ).detect(.zed, mcpURL: "http://127.0.0.1:3010/mcp")

        XCTAssertEqual(detection.state, .configuredDifferentURL)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReadsZedJSONCSettings() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "zed", in: directory)
        let zedConfigURL = directory.appendingPathComponent(".config/zed/settings.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeZedJSONCSettings(to: zedConfigURL, mcpURL: mcpURL)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            zedConfigurationURL: zedConfigURL
        ).detect(.zed, mcpURL: mcpURL)

        XCTAssertEqual(detection.state, .configured)
        XCTAssertTrue(runner.recordedInvocations.isEmpty)
    }

    func testAgentDetectorReportsZedMCPWithoutAuthorizationHeaderAsUpdateNeeded() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        _ = try makeExecutable(named: "zed", in: directory)
        let zedConfigURL = directory.appendingPathComponent(".config/zed/settings.json")
        let mcpURL = "http://127.0.0.1:3010/mcp"
        try writeZedSettings(to: zedConfigURL, mcpURL: mcpURL, includeAuthorizationHeader: false)
        let runner = FakeProcessRunner(responses: [:])

        let detection = OpenPetsAgentDetector(
            processRunner: runner,
            shellURL: URL(fileURLWithPath: "/bin/zsh"),
            searchDirectories: [directory],
            zedConfigurationURL: zedConfigURL
        ).detect(.zed, mcpURL: mcpURL)

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

    func testAgentDetectorIncludesOpenCodeInstallDirectoryInDefaultSearchDirectories() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let expectedURL = directory.appendingPathComponent(".opencode/bin", isDirectory: true)

        let searchDirectories = OpenPetsAgentDetector.defaultSearchDirectories(homeDirectoryURL: directory)

        XCTAssertTrue(searchDirectories.contains { $0.standardizedFileURL.path == expectedURL.standardizedFileURL.path })
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
        XCTAssertEqual(
            installer.command(
                kind: .pi,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/pi"),
                mcpURL: mcpURL
            ).arguments,
            ["install", "npm:pi-mcp-extension"]
        )
        XCTAssertEqual(
            installer.command(
                kind: .openCode,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode"),
                mcpURL: mcpURL
            ).arguments,
            []
        )
        XCTAssertEqual(
            installer.command(
                kind: .zed,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/zed"),
                mcpURL: mcpURL
            ).arguments,
            []
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
        XCTAssertEqual(
            installer.uninstallCommand(
                kind: .pi,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/pi")
            ).arguments,
            []
        )
        XCTAssertEqual(
            installer.uninstallCommand(
                kind: .openCode,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode")
            ).arguments,
            []
        )
        XCTAssertEqual(
            installer.uninstallCommand(
                kind: .zed,
                executableURL: URL(fileURLWithPath: "/usr/local/bin/zed")
            ).arguments,
            []
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

    func testAgentSetupInstallerInstallsPiExtensionAndWritesMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".pi/agent/mcp.json")
        try writePiMCPConfig(to: configURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let commandKey = FakeProcessRunner.key(
            "/usr/local/bin/pi",
            ["install", "npm:pi-mcp-extension"]
        )
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [
                commandKey: .success("installed")
            ]),
            piMCPConfigurationURL: configURL
        )

        let result = try installer.install(
            kind: .pi,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/pi"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        let configuredURL = try mcpServerURL(in: configURL, sectionKey: "mcpServers", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
    }

    func testAgentSetupInstallerWritesOpenCodeMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        try writeOpenCodeConfig(to: configURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            openCodeConfigurationURL: configURL
        )

        let result = try installer.install(
            kind: .openCode,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        let configuredURL = try mcpServerURL(in: configURL, sectionKey: "mcp", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
    }

    func testAgentSetupInstallerUpdatesOpenCodeJSONCConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        try writeOpenCodeJSONCConfig(to: configURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            openCodeConfigurationURL: configURL
        )

        let result = try installer.install(
            kind: .openCode,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        let configuredURL = try mcpServerURL(in: configURL, sectionKey: "mcp", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
    }

    func testAgentSetupInstallerUpdatesExistingOpenCodeJSONCFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let jsonURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let jsoncURL = directory.appendingPathComponent(".config/opencode/opencode.jsonc")
        try writeOpenCodeJSONCConfig(to: jsoncURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            openCodeConfigurationURL: jsonURL
        )

        let result = try installer.install(
            kind: .openCode,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertFalse(FileManager.default.fileExists(atPath: jsonURL.path))
        let configuredURL = try mcpServerURL(in: jsoncURL, sectionKey: "mcp", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
    }

    func testAgentSetupInstallerPrefersExistingOpenCodeJSONCFile() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let jsonURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        let jsoncURL = directory.appendingPathComponent(".config/opencode/opencode.jsonc")
        try writeOpenCodeConfig(to: jsonURL, mcpURL: "http://127.0.0.1:3001/mcp")
        try writeOpenCodeJSONCConfig(to: jsoncURL, mcpURL: "http://127.0.0.1:3002/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            openCodeConfigurationURL: jsonURL
        )

        let result = try installer.install(
            kind: .openCode,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertEqual(
            try mcpServerURL(in: jsonURL, sectionKey: "mcp", name: "openpets"),
            "http://127.0.0.1:3001/mcp"
        )
        XCTAssertEqual(
            try mcpServerURL(in: jsoncURL, sectionKey: "mcp", name: "openpets"),
            "http://127.0.0.1:3010/mcp"
        )
    }

    func testAgentSetupInstallerWritesZedMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/zed/settings.json")
        try writeZedSettings(to: configURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            zedConfigurationURL: configURL
        )

        let result = try installer.install(
            kind: .zed,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/zed"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        let configuredURL = try mcpServerURL(in: configURL, sectionKey: "context_servers", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
        XCTAssertEqual(
            try mcpServerHeader(in: configURL, sectionKey: "context_servers", name: "openpets", header: "Authorization"),
            "Bearer openpets-local"
        )
    }

    func testAgentSetupInstallerUpdatesZedJSONCSettings() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/zed/settings.json")
        try writeZedJSONCSettings(to: configURL, mcpURL: "http://127.0.0.1:3001/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            zedConfigurationURL: configURL
        )

        let result = try installer.install(
            kind: .zed,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/zed"),
            mcpURL: "http://127.0.0.1:3010/mcp"
        )

        XCTAssertTrue(result.succeeded)
        let configuredURL = try mcpServerURL(in: configURL, sectionKey: "context_servers", name: "openpets")
        XCTAssertEqual(configuredURL, "http://127.0.0.1:3010/mcp")
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

    func testAgentSetupInstallerUninstallsPiMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".pi/agent/mcp.json")
        try writePiMCPConfig(to: configURL, mcpURL: "http://127.0.0.1:3010/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            piMCPConfigurationURL: configURL
        )

        let result = try installer.uninstall(
            kind: .pi,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/pi")
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertNil(try mcpServerURL(in: configURL, sectionKey: "mcpServers", name: "openpets"))
        XCTAssertEqual(result.message, "Pi MCP setup removed.")
    }

    func testAgentSetupInstallerUninstallsOpenCodeMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/opencode/opencode.json")
        try writeOpenCodeConfig(to: configURL, mcpURL: "http://127.0.0.1:3010/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            openCodeConfigurationURL: configURL
        )

        let result = try installer.uninstall(
            kind: .openCode,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/opencode")
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertNil(try mcpServerURL(in: configURL, sectionKey: "mcp", name: "openpets"))
        XCTAssertEqual(result.message, "OpenCode MCP setup removed.")
    }

    func testAgentSetupInstallerUninstallsZedMCPConfig() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let configURL = directory.appendingPathComponent(".config/zed/settings.json")
        try writeZedSettings(to: configURL, mcpURL: "http://127.0.0.1:3010/mcp")
        let installer = OpenPetsAgentSetupInstaller(
            processRunner: FakeProcessRunner(responses: [:]),
            zedConfigurationURL: configURL
        )

        let result = try installer.uninstall(
            kind: .zed,
            executableURL: URL(fileURLWithPath: "/usr/local/bin/zed")
        )

        XCTAssertTrue(result.succeeded)
        XCTAssertNil(try mcpServerURL(in: configURL, sectionKey: "context_servers", name: "openpets"))
        XCTAssertEqual(result.message, "Zed MCP setup removed.")
    }

    func testAssistantInstructionsTargetsIncludePi() throws {
        let targets = OpenPetsAssistantInstructions.globalInstructionTargets(for: [.pi])

        XCTAssertEqual(targets.first?.kind, .pi)
        XCTAssertEqual(targets.first?.displayName, "Pi global instructions")
        XCTAssertEqual(targets.first?.fileURL.lastPathComponent, "AGENTS.md")
        XCTAssertTrue(targets.first?.fileURL.path.contains(".pi/agent") == true)
    }

    func testAssistantInstructionsTargetsIncludeOpenCode() throws {
        let targets = OpenPetsAssistantInstructions.globalInstructionTargets(for: [.openCode])

        XCTAssertEqual(targets.first?.kind, .openCode)
        XCTAssertEqual(targets.first?.displayName, "OpenCode global instructions")
        XCTAssertEqual(targets.first?.fileURL.lastPathComponent, "AGENTS.md")
        XCTAssertTrue(targets.first?.fileURL.path.contains(".config/opencode") == true)
    }

    func testAssistantInstructionsTargetsExcludeZed() throws {
        let targets = OpenPetsAssistantInstructions.globalInstructionTargets(for: [.zed])

        XCTAssertTrue(targets.isEmpty)
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

    private func makeTemporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("openpets-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    private func withTemporaryXDGConfigHome<T>(_ body: () throws -> T) throws -> T {
        let originalValue = getenv("XDG_CONFIG_HOME").map { String(cString: $0) }
        let directory = try makeTemporaryDirectory()
        setenv("XDG_CONFIG_HOME", directory.path, 1)
        defer {
            if let originalValue {
                setenv("XDG_CONFIG_HOME", originalValue, 1)
            } else {
                unsetenv("XDG_CONFIG_HOME")
            }
            try? FileManager.default.removeItem(at: directory)
        }
        return try body()
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

    private func writePiMCPConfig(to url: URL, mcpURL: String) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let object: [String: Any] = [
            "settings": [
                "toolPrefix": "mcp"
            ],
            "mcpServers": [
                "openpets": [
                    "transport": "streamable-http",
                    "url": mcpURL,
                    "lifecycle": "eager"
                ],
                "other": [
                    "transport": "streamable-http",
                    "url": "https://example.test/mcp"
                ]
            ]
        ]
        let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }

    private func writeOpenCodeConfig(to url: URL, mcpURL: String, enabled: Bool = true) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let object: [String: Any] = [
            "$schema": "https://opencode.ai/config.json",
            "theme": "opencode",
            "mcp": [
                "openpets": [
                    "type": "remote",
                    "url": mcpURL,
                    "enabled": enabled
                ],
                "other": [
                    "type": "remote",
                    "url": "https://example.test/mcp"
                ]
            ]
        ]
        let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }

    private func writeOpenCodeJSONCConfig(to url: URL, mcpURL: String) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(
            """
            {
              // OpenCode allows comments in config files.
              "$schema": "https://opencode.ai/config.json",
              "theme": "opencode",
              "mcp": {
                "openpets": {
                  "type": "remote",
                  "url": "\(mcpURL)",
                  "enabled": true,
                },
              },
            }
            """.utf8
        ).write(to: url)
    }

    private func writeZedSettings(
        to url: URL,
        mcpURL: String,
        includeAuthorizationHeader: Bool = true
    ) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var openPetsServer: [String: Any] = [
            "url": mcpURL
        ]
        if includeAuthorizationHeader {
            openPetsServer["headers"] = [
                "Authorization": "Bearer openpets-local"
            ]
        }
        let object: [String: Any] = [
            "theme": "Ayu Dark",
            "context_servers": [
                "openpets": openPetsServer,
                "other": [
                    "url": "https://example.test/mcp"
                ]
            ]
        ]
        let data = try JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url)
    }

    private func writeZedJSONCSettings(to url: URL, mcpURL: String) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(
            """
            // Zed settings support JSONC comments.
            {
              "theme": "Ayu Dark",
              "context_servers": {
                "openpets": {
                  "url": "\(mcpURL)",
                  "headers": {
                    "Authorization": "Bearer openpets-local",
                  },
                },
              },
            }
            """.utf8
        ).write(to: url)
    }

    private func mcpServerURL(in url: URL, sectionKey: String, name: String) throws -> String? {
        let data = try Data(contentsOf: url)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let servers = try XCTUnwrap(json[sectionKey] as? [String: Any])
        let server = servers[name] as? [String: Any]
        return server?["url"] as? String
    }

    private func mcpServerHeader(in url: URL, sectionKey: String, name: String, header: String) throws -> String? {
        let data = try Data(contentsOf: url)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let servers = try XCTUnwrap(json[sectionKey] as? [String: Any])
        let server = try XCTUnwrap(servers[name] as? [String: Any])
        let headers = server["headers"] as? [String: Any]
        return headers?[header] as? String
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

    private func menuItemTitles(_ menu: NSMenu) -> [String] {
        menu.items.map { item in
            item.isSeparatorItem ? "<separator>" : item.title
        }
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

private final class RequestRecorder: @unchecked Sendable {
    var request: URLRequest?
}

private extension OpenPetsProcessResult {
    static func success(_ output: String) -> OpenPetsProcessResult {
        OpenPetsProcessResult(terminationStatus: 0, standardOutput: output, standardError: "")
    }

    static func failure(_ error: String) -> OpenPetsProcessResult {
        OpenPetsProcessResult(terminationStatus: 1, standardOutput: "", standardError: error)
    }
}
