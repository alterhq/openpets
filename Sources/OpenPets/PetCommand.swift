import Foundation

public struct PetNotification: Codable, Equatable, Sendable {
    public var title: String
    public var text: String?
    public var status: String
    public var threadId: String?
    public var url: String?
    public var buttonLabel: String?
    public var ttlSeconds: Double?

    public init(
        title: String,
        text: String? = nil,
        status: String,
        threadId: String? = nil,
        url: String? = nil,
        buttonLabel: String? = nil,
        ttlSeconds: Double? = nil
    ) {
        self.title = title
        self.text = text
        self.status = status
        self.threadId = threadId
        self.url = url
        self.buttonLabel = buttonLabel
        self.ttlSeconds = ttlSeconds
    }

    private enum CodingKeys: String, CodingKey {
        case title
        case text
        case status
        case threadId
        case url
        case buttonLabel
        case ttlSeconds
    }
}

public extension PetNotification {
    func resolvingThreadId() -> PetNotification {
        guard threadId?.isEmpty != false else {
            return self
        }

        var notification = self
        notification.threadId = UUID().uuidString
        return notification
    }
}

public enum PetCommand: Equatable, Sendable {
    case notify(PetNotification)
    case playAnimation(name: PetAnimation, loop: Bool?, ttlSeconds: Double?)
    case stopAnimation
    case clearMessage(threadId: String)
    case ping
    case shutdown
}

extension PetCommand: Codable {
    private enum CodingKeys: String, CodingKey {
        case type
        case notification
        case ttlSeconds
        case name
        case loop
        case threadId
    }

    private enum CommandType: String, Codable {
        case notify
        case playAnimation
        case stopAnimation
        case clearMessage
        case ping
        case shutdown
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(CommandType.self, forKey: .type)

        switch type {
        case .notify:
            self = .notify(try container.decode(PetNotification.self, forKey: .notification))
        case .playAnimation:
            self = .playAnimation(
                name: try container.decode(PetAnimation.self, forKey: .name),
                loop: try container.decodeIfPresent(Bool.self, forKey: .loop),
                ttlSeconds: try container.decodeIfPresent(Double.self, forKey: .ttlSeconds)
            )
        case .stopAnimation:
            self = .stopAnimation
        case .clearMessage:
            self = .clearMessage(threadId: try container.decode(String.self, forKey: .threadId))
        case .ping:
            self = .ping
        case .shutdown:
            self = .shutdown
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)

        switch self {
        case .notify(let notification):
            try container.encode(CommandType.notify, forKey: .type)
            try container.encode(notification, forKey: .notification)
        case .playAnimation(let name, let loop, let ttlSeconds):
            try container.encode(CommandType.playAnimation, forKey: .type)
            try container.encode(name, forKey: .name)
            try container.encodeIfPresent(loop, forKey: .loop)
            try container.encodeIfPresent(ttlSeconds, forKey: .ttlSeconds)
        case .stopAnimation:
            try container.encode(CommandType.stopAnimation, forKey: .type)
        case .clearMessage(let threadId):
            try container.encode(CommandType.clearMessage, forKey: .type)
            try container.encode(threadId, forKey: .threadId)
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
    public var threadId: String?

    public init(ok: Bool, message: String? = nil, threadId: String? = nil) {
        self.ok = ok
        self.message = message
        self.threadId = threadId
    }
}
