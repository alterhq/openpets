import Foundation

public struct PetStatus: Codable, Equatable, Sendable {
    public var kind: String
    public var message: String?
    public var ttlSeconds: Double?

    public init(kind: String, message: String? = nil, ttlSeconds: Double? = nil) {
        self.kind = kind
        self.message = message
        self.ttlSeconds = ttlSeconds
    }
}

public enum PetCommand: Equatable, Sendable {
    case setMessage(text: String, ttlSeconds: Double?, priority: Int?)
    case setStatus(kind: String, message: String?, ttlSeconds: Double?)
    case playAnimation(name: PetAnimation, loop: Bool?, ttlSeconds: Double?)
    case clearMessage
    case ping
    case shutdown
}

extension PetCommand: Codable {
    private enum CodingKeys: String, CodingKey {
        case type
        case text
        case ttlSeconds
        case priority
        case kind
        case message
        case name
        case loop
    }

    private enum CommandType: String, Codable {
        case setMessage
        case setStatus
        case playAnimation
        case clearMessage
        case ping
        case shutdown
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(CommandType.self, forKey: .type)

        switch type {
        case .setMessage:
            self = .setMessage(
                text: try container.decode(String.self, forKey: .text),
                ttlSeconds: try container.decodeIfPresent(Double.self, forKey: .ttlSeconds),
                priority: try container.decodeIfPresent(Int.self, forKey: .priority)
            )
        case .setStatus:
            self = .setStatus(
                kind: try container.decode(String.self, forKey: .kind),
                message: try container.decodeIfPresent(String.self, forKey: .message),
                ttlSeconds: try container.decodeIfPresent(Double.self, forKey: .ttlSeconds)
            )
        case .playAnimation:
            self = .playAnimation(
                name: try container.decode(PetAnimation.self, forKey: .name),
                loop: try container.decodeIfPresent(Bool.self, forKey: .loop),
                ttlSeconds: try container.decodeIfPresent(Double.self, forKey: .ttlSeconds)
            )
        case .clearMessage:
            self = .clearMessage
        case .ping:
            self = .ping
        case .shutdown:
            self = .shutdown
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)

        switch self {
        case .setMessage(let text, let ttlSeconds, let priority):
            try container.encode(CommandType.setMessage, forKey: .type)
            try container.encode(text, forKey: .text)
            try container.encodeIfPresent(ttlSeconds, forKey: .ttlSeconds)
            try container.encodeIfPresent(priority, forKey: .priority)
        case .setStatus(let kind, let message, let ttlSeconds):
            try container.encode(CommandType.setStatus, forKey: .type)
            try container.encode(kind, forKey: .kind)
            try container.encodeIfPresent(message, forKey: .message)
            try container.encodeIfPresent(ttlSeconds, forKey: .ttlSeconds)
        case .playAnimation(let name, let loop, let ttlSeconds):
            try container.encode(CommandType.playAnimation, forKey: .type)
            try container.encode(name, forKey: .name)
            try container.encodeIfPresent(loop, forKey: .loop)
            try container.encodeIfPresent(ttlSeconds, forKey: .ttlSeconds)
        case .clearMessage:
            try container.encode(CommandType.clearMessage, forKey: .type)
        case .ping:
            try container.encode(CommandType.ping, forKey: .type)
        case .shutdown:
            try container.encode(CommandType.shutdown, forKey: .type)
        }
    }
}

public struct PetResponse: Codable, Equatable, Sendable {
    public var ok: Bool
    public var message: String?

    public init(ok: Bool, message: String? = nil) {
        self.ok = ok
        self.message = message
    }
}
