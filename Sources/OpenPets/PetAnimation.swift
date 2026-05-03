import Foundation

public enum PetAnimation: String, Codable, CaseIterable, Sendable {
    case idle
    case runningRight = "running-right"
    case runningLeft = "running-left"
    case waving
    case jumping
    case failed
    case waiting
    case running
    case review

    public var row: Int {
        switch self {
        case .idle: 0
        case .runningRight: 1
        case .runningLeft: 2
        case .waving: 3
        case .jumping: 4
        case .failed: 5
        case .waiting: 6
        case .running: 7
        case .review: 8
        }
    }

    public var frameCount: Int {
        frameDurationsMilliseconds.count
    }

    public var frameDurationsMilliseconds: [Int] {
        switch self {
        case .idle: [2_000, 880, 820, 880, 820, 2_600]
        case .runningRight: [120, 120, 120, 120, 120, 120, 120, 220]
        case .runningLeft: [120, 120, 120, 120, 120, 120, 120, 220]
        case .waving: [140, 140, 140, 280]
        case .jumping: [140, 140, 140, 140, 280]
        case .failed: [140, 140, 140, 140, 140, 140, 140, 240]
        case .waiting: [150, 150, 150, 150, 150, 280]
        case .running: [150, 150, 150, 150, 150, 280]
        case .review: [150, 150, 150, 150, 150, 280]
        }
    }

    public init?(cliValue: String) {
        if let animation = PetAnimation(rawValue: cliValue) {
            self = animation
            return
        }

        switch cliValue {
        case "runningRight", "right":
            self = .runningRight
        case "runningLeft", "left":
            self = .runningLeft
        default:
            return nil
        }
    }
}
