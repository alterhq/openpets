import CoreGraphics

public struct OpenPetsDisplayConfiguration: Codable, Equatable, Sendable {
    public static let defaultScale: CGFloat = 1.0 / 3.0
    public static let `default` = OpenPetsDisplayConfiguration()

    public var scale: CGFloat
    public var messageAreaHeight: CGFloat

    public init(
        scale: CGFloat = OpenPetsDisplayConfiguration.defaultScale,
        messageAreaHeight: CGFloat = 56
    ) {
        self.scale = scale
        self.messageAreaHeight = messageAreaHeight
    }
}
