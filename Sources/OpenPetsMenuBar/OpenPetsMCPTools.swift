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

private func openPetsTools() -> [Tool] {
    [
        Tool(
            name: "get_openpets_status",
            description: "Get OpenPets MCP server and pet status.",
            inputSchema: objectSchema(),
            annotations: .init(readOnlyHint: true)
        ),
        Tool(
            name: "wake_pet",
            description: "Wake the OpenPets desktop pet.",
            inputSchema: objectSchema(),
            annotations: .init(destructiveHint: false, idempotentHint: true)
        ),
        Tool(
            name: "stop_pet",
            description: "Stop the OpenPets desktop pet.",
            inputSchema: objectSchema(),
            annotations: .init(destructiveHint: true, idempotentHint: true)
        ),
        Tool(
            name: "set_pet_message",
            description: "Show a message bubble on the OpenPets desktop pet.",
            inputSchema: objectSchema(
                properties: [
                    "text": property(type: "string", description: "Message text to show."),
                    "ttlSeconds": property(type: "number", description: "Optional lifetime in seconds."),
                    "priority": property(type: "integer", description: "Optional priority value.")
                ],
                required: ["text"]
            )
        ),
        Tool(
            name: "set_pet_status",
            description: "Set a status on the OpenPets desktop pet.",
            inputSchema: objectSchema(
                properties: [
                    "kind": property(type: "string", description: "Status kind, such as running, review, done, or failed."),
                    "message": property(type: "string", description: "Optional status detail."),
                    "ttlSeconds": property(type: "number", description: "Optional lifetime in seconds.")
                ],
                required: ["kind"]
            )
        ),
        Tool(
            name: "play_pet_animation",
            description: "Play one OpenPets animation.",
            inputSchema: objectSchema(
                properties: [
                    "name": property(
                        type: "string",
                        description: "Animation name: idle, running-right, running-left, waving, jumping, failed, waiting, running, or review."
                    ),
                    "loop": property(type: "boolean", description: "Whether to loop the animation."),
                    "ttlSeconds": property(type: "number", description: "Optional lifetime in seconds.")
                ],
                required: ["name"]
            )
        ),
        Tool(
            name: "clear_pet_message",
            description: "Clear the OpenPets message bubble.",
            inputSchema: objectSchema()
        ),
        Tool(
            name: "ping_pet",
            description: "Ping the OpenPets desktop pet.",
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

        case "set_pet_message":
            guard let text = arguments["text"]?.stringValue, !text.isEmpty else {
                return failure("Missing required string argument: text")
            }
            return await commandResult(controller.sendPetCommand(.setMessage(
                text: text,
                ttlSeconds: number(arguments["ttlSeconds"]),
                priority: arguments["priority"]?.intValue
            )))

        case "set_pet_status":
            guard let kind = arguments["kind"]?.stringValue, !kind.isEmpty else {
                return failure("Missing required string argument: kind")
            }
            return await commandResult(controller.sendPetCommand(.setStatus(
                kind: kind,
                message: arguments["message"]?.stringValue,
                ttlSeconds: number(arguments["ttlSeconds"])
            )))

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

private func property(type: String, description: String) -> Value {
    .object([
        "type": .string(type),
        "description": .string(description)
    ])
}

private func number(_ value: Value?) -> Double? {
    value?.doubleValue ?? value?.intValue.map(Double.init)
}
