import Foundation
import OpenPetsCore

@main
struct OpenPetsCLI {
    @MainActor
    static func main() {
        do {
            try run(arguments: Array(CommandLine.arguments.dropFirst()))
        } catch {
            FileHandle.standardError.write(Data("openpets: \(error.localizedDescription)\n".utf8))
            Foundation.exit(1)
        }
    }

    @MainActor
    private static func run(arguments: [String]) throws {
        guard let command = arguments.first else {
            printUsage()
            return
        }

        switch command {
        case "run":
            let options = parseOptions(Array(arguments.dropFirst()))
            guard let petPath = options.values["pet"] else {
                throw CLIError.missingRequiredOption("--pet")
            }
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let socketPath = options.values["socket"] ?? userConfiguration.socketPath
            var display = userConfiguration.display
            if let scale = options.values["scale"].flatMap(Double.init).map({ CGFloat($0) }) {
                display.scale = scale
            }
            let configuration = OpenPetsHostConfiguration(
                petDirectoryURL: URL(fileURLWithPath: petPath).standardizedFileURL,
                socketPath: socketPath,
                display: display
            )
            try OpenPetsHost.run(configuration: configuration)

        case "send":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let parsed = parseOptionsAndPositionals(Array(arguments.dropFirst()))
            let message = parsed.positionals.joined(separator: " ")
            guard !message.isEmpty else {
                throw CLIError.missingArgument("message")
            }
            try send(
                .setMessage(
                    text: message,
                    ttlSeconds: parsed.values["ttl"].flatMap(Double.init),
                    priority: parsed.values["priority"].flatMap(Int.init)
                ),
                socketPath: parsed.values["socket"] ?? userConfiguration.socketPath
            )

        case "status":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let parsed = parseOptionsAndPositionals(Array(arguments.dropFirst()))
            guard let kind = parsed.positionals.first else {
                throw CLIError.missingArgument("status kind")
            }
            try send(
                .setStatus(
                    kind: kind,
                    message: parsed.values["message"],
                    ttlSeconds: parsed.values["ttl"].flatMap(Double.init)
                ),
                socketPath: parsed.values["socket"] ?? userConfiguration.socketPath
            )

        case "animate":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let parsed = parseOptionsAndPositionals(Array(arguments.dropFirst()))
            guard let animationName = parsed.positionals.first else {
                throw CLIError.missingArgument("animation")
            }
            guard let animation = PetAnimation(cliValue: animationName) else {
                throw CLIError.invalidArgument("Unknown animation '\(animationName)'")
            }
            let loop = parsed.flags.contains("loop") ? true : (parsed.flags.contains("once") ? false : nil)
            try send(
                .playAnimation(
                    name: animation,
                    loop: loop,
                    ttlSeconds: parsed.values["ttl"].flatMap(Double.init)
                ),
                socketPath: parsed.values["socket"] ?? userConfiguration.socketPath
            )

        case "clear":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let options = parseOptions(Array(arguments.dropFirst()))
            try send(.clearMessage, socketPath: options.values["socket"] ?? userConfiguration.socketPath)

        case "ping":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let options = parseOptions(Array(arguments.dropFirst()))
            try send(.ping, socketPath: options.values["socket"] ?? userConfiguration.socketPath)

        case "stop":
            let userConfiguration = try OpenPetsConfiguration.loadOrCreateDefault()
            let options = parseOptions(Array(arguments.dropFirst()))
            try send(.shutdown, socketPath: options.values["socket"] ?? userConfiguration.socketPath)

        case "help", "--help", "-h":
            printUsage()

        default:
            throw CLIError.invalidArgument("Unknown command '\(command)'")
        }
    }

    private static func send(_ command: PetCommand, socketPath: String?) throws {
        let response = try OpenPetsClient(socketPath: socketPath ?? OpenPetsPaths.defaultSocketPath).send(command)
        if let message = response.message, !message.isEmpty {
            print(message)
        } else if !response.ok {
            print("failed")
        }
        if !response.ok {
            Foundation.exit(2)
        }
    }

    private static func printUsage() {
        print(
            """
            Usage:
              openpets run --pet /Users/sam/.codex/pets/starcorn [--socket PATH] [--scale 0.42]
              openpets send "message text" [--ttl SECONDS] [--priority N] [--socket PATH]
              openpets status KIND [--message TEXT] [--ttl SECONDS] [--socket PATH]
              openpets animate ANIMATION [--loop|--once] [--ttl SECONDS] [--socket PATH]
              openpets clear [--socket PATH]
              openpets ping [--socket PATH]
              openpets stop [--socket PATH]

            Animations: idle, running-right, running-left, waving, jumping, failed, review
            """
        )
    }

    private static func parseOptions(_ arguments: [String]) -> ParsedArguments {
        parseOptionsAndPositionals(arguments)
    }

    private static func parseOptionsAndPositionals(_ arguments: [String]) -> ParsedArguments {
        var values: [String: String] = [:]
        var flags = Set<String>()
        var positionals: [String] = []
        var index = 0

        while index < arguments.count {
            let argument = arguments[index]
            if argument.hasPrefix("--") {
                let name = String(argument.dropFirst(2))
                if ["loop", "once"].contains(name) {
                    flags.insert(name)
                    index += 1
                    continue
                }
                if index + 1 < arguments.count {
                    values[name] = arguments[index + 1]
                    index += 2
                } else {
                    flags.insert(name)
                    index += 1
                }
            } else {
                positionals.append(argument)
                index += 1
            }
        }

        return ParsedArguments(values: values, flags: flags, positionals: positionals)
    }
}

private struct ParsedArguments {
    var values: [String: String]
    var flags: Set<String>
    var positionals: [String]
}

private enum CLIError: Error, LocalizedError {
    case missingRequiredOption(String)
    case missingArgument(String)
    case invalidArgument(String)

    var errorDescription: String? {
        switch self {
        case .missingRequiredOption(let option):
            "Missing required option \(option)"
        case .missingArgument(let argument):
            "Missing required argument: \(argument)"
        case .invalidArgument(let message):
            message
        }
    }
}
