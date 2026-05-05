import AppKit

@MainActor
enum OpenPetsAppIcon {
    nonisolated static let resourceName = "AppIcon"
    nonisolated static let resourceExtension = "icns"

    static var image: NSImage {
        if
            let iconURL = Bundle.main.url(forResource: resourceName, withExtension: resourceExtension),
            let icon = NSImage(contentsOf: iconURL)
        {
            return icon
        }

        if let icon = NSImage(named: NSImage.Name(resourceName)) {
            return icon
        }

        return NSImage(named: NSImage.applicationIconName) ?? NSImage()
    }

    static func install(on application: NSApplication = .shared) {
        application.applicationIconImage = image
    }

    static func apply(to alert: NSAlert) {
        alert.icon = image
    }
}
