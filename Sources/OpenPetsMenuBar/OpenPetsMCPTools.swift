import Foundation
import MCP
import OpenPetsCore

func makeOpenPetsMCPServer(controller: OpenPetsMenuBarController) async -> Server {
    let server = Server(
        name: "openpets",
        version: "1.0.0",
        capabilities: .init(tools: .init(listChanged: true))
    )

    await server.withMethodHandler(ListTools.self) { _ in
        .init(tools: openPetsTools())
    }

    await server.withMethodHandler(CallTool.self) { params in
        await callOpenPetsTool(params, controller: controller)
    }

    return server
}

let openPetsStatusValues = [
    "running",
    "review",
    "done",
    "failed",
    "waiting",
    "message"
]

let openPetsAnimationValues = [
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review"
]

func openPetsTools() -> [Tool] {
    [
        Tool(
            name: "get_openpets_status",
            description: "Check whether the OpenPets MCP server is running and whether the desktop pet is awake. Use this before sending pet commands if you are unsure whether the pet is available.",
            inputSchema: objectSchema(),
            annotations: .init(readOnlyHint: true)
        ),
        Tool(
            name: "wake_pet",
            description: "Start or bring back the OpenPets desktop pet. Use this before notify or animation tools when the pet is not running.",
            inputSchema: objectSchema(),
            annotations: .init(destructiveHint: false, idempotentHint: true)
        ),
        Tool(
            name: "stop_pet",
            description: "Stop the OpenPets desktop pet. Use only when the user asks to hide, quit, stop, or dismiss the pet.",
            inputSchema: objectSchema(),
            annotations: .init(destructiveHint: true, idempotentHint: true)
        ),
        Tool(
            name: "notify",
            description: "Show a message bubble above the OpenPets desktop pet and choose a status-driven animation. Use this to tell the user about task progress, completion, errors, review needs, or replies. This tool automatically wakes the pet if needed; if delivery fails, it returns the current OpenPets status.",
            inputSchema: objectSchema(
                properties: [
                    "title": property(type: "string", description: "Short message title shown in the pet bubble."),
                    "text": property(
                        type: "string",
                        description: "Message body shown under the title. Keep it concise; use the current task result or next action."
                    ),
                    "status": property(
                        type: "string",
                        description: "Required status that controls the pet animation and bubble indicator. Valid values: \(openPetsStatusValues.joined(separator: ", ")).",
                        enumValues: openPetsStatusValues
                    ),
                    "x-url-callback": property(
                        type: "string",
                        description: "Optional URL opened when the bubble action button is clicked. Use only for an actionable destination."
                    ),
                    "buttonLabel": property(
                        type: "string",
                        description: "Optional action button label, such as Open, Reply, Review, or View."
                    ),
                    "ttlSeconds": property(
                        type: "number",
                        description: "Optional number of seconds before the message auto-clears. Omit to keep the message visible until replaced or cleared."
                    )
                ],
                required: ["title", "text", "status"]
            )
        ),
        Tool(
            name: "play_pet_animation",
            description: "Play a pet animation without showing a message. Use notify instead when you need to communicate text to the user.",
            inputSchema: objectSchema(
                properties: [
                    "name": property(
                        type: "string",
                        description: "Animation to play. Valid values: \(openPetsAnimationValues.joined(separator: ", ")). Aliases accepted by the implementation: right, left, runningRight, runningLeft.",
                        enumValues: openPetsAnimationValues
                    ),
                    "loop": property(type: "boolean", description: "Whether to loop the animation. Defaults to true when omitted."),
                    "ttlSeconds": property(
                        type: "number",
                        description: "Optional number of seconds before the animation returns to idle. Useful for temporary non-message animations."
                    )
                ],
                required: ["name"]
            )
        ),
        Tool(
            name: "clear_pet_message",
            description: "Clear the current OpenPets message bubble without stopping the pet.",
            inputSchema: objectSchema()
        ),
        Tool(
            name: "ping_pet",
            description: "Ping the desktop pet process to confirm it can receive commands. This is a lightweight connectivity check.",
            inputSchema: objectSchema(),
            annotations: .init(readOnlyHint: true)
        )
    ]
}

private func callOpenPetsTool(
    _ params: CallTool.Parameters,
    controller: OpenPetsMenuBarController
) async -> CallTool.Result {
    let arguments = params.arguments ?? [:]

    do {
        switch params.name {
        case "get_openpets_status":
            return await controller.mcpStatusResult()

        case "wake_pet":
            let message = try await controller.wakePetForMCP()
            return ok(message)

        case "stop_pet":
            let message = await controller.stopPetForMCP()
            return ok(message)

        case "notify":
            guard let title = arguments["title"]?.stringValue, !title.isEmpty else {
                return failure("Missing required string argument: title")
            }
            guard let text = arguments["text"]?.stringValue, !text.isEmpty else {
                return failure("Missing required string argument: text")
            }
            guard let status = arguments["status"]?.stringValue, !status.isEmpty else {
                return failure("Missing required string argument: status")
            }
            let notification = PetNotification(
                title: title,
                text: text,
                status: status,
                xURLCallback: arguments["x-url-callback"]?.stringValue,
                buttonLabel: arguments["buttonLabel"]?.stringValue,
                ttlSeconds: number(arguments["ttlSeconds"])
            )
            let response = try await controller.notifyForMCP(notification)
            return commandResult(response)

        case "play_pet_animation":
            guard let name = arguments["name"]?.stringValue, let animation = PetAnimation(cliValue: name) else {
                return failure("Missing or invalid animation name")
            }
            return await commandResult(controller.sendPetCommand(.playAnimation(
                name: animation,
                loop: arguments["loop"]?.boolValue,
                ttlSeconds: number(arguments["ttlSeconds"])
            )))

        case "clear_pet_message":
            return await commandResult(controller.sendPetCommand(.clearMessage))

        case "ping_pet":
            return await commandResult(controller.sendPetCommand(.ping))

        default:
            return failure("Unknown tool: \(params.name)")
        }
    } catch {
        return failure(error.localizedDescription)
    }
}

private func commandResult(_ response: PetResponse) -> CallTool.Result {
    if response.ok {
        return ok(response.message ?? "ok")
    }
    return failure(response.message ?? "OpenPets command failed")
}

private func ok(_ message: String) -> CallTool.Result {
    .init(content: [.text(text: message, annotations: nil, _meta: nil)], isError: false)
}

private func failure(_ message: String) -> CallTool.Result {
    .init(content: [.text(text: message, annotations: nil, _meta: nil)], isError: true)
}

private func objectSchema(properties: [String: Value] = [:], required: [String] = []) -> Value {
    var schema: [String: Value] = [
        "type": .string("object"),
        "properties": .object(properties)
    ]
    if !required.isEmpty {
        schema["required"] = .array(required.map { .string($0) })
    }
    return .object(schema)
}

private func property(type: String, description: String, enumValues: [String] = []) -> Value {
    var schema: [String: Value] = [
        "type": .string(type),
        "description": .string(description)
    ]
    if !enumValues.isEmpty {
        schema["enum"] = .array(enumValues.map { .string($0) })
    }
    return .object(schema)
}

private func number(_ value: Value?) -> Double? {
    value?.doubleValue ?? value?.intValue.map(Double.init)
}
