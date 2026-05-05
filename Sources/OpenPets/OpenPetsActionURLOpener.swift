import AppKit
import Foundation

struct OpenPetsActionURLOpener {
    typealias Completion = @Sendable (NSRunningApplication?, Error?) -> Void
    typealias WorkspaceOpen = @Sendable (URL, NSWorkspace.OpenConfiguration, @escaping Completion) -> Void

    private let workspaceOpen: WorkspaceOpen

    init(workspaceOpen: @escaping WorkspaceOpen = OpenPetsActionURLOpener.defaultWorkspaceOpen) {
        self.workspaceOpen = workspaceOpen
    }

    func open(_ url: URL) {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.activates = true
        let urlDescription = Self.traceDescription(for: url)

        NSLog("%@", "OpenPets launching action URL: \(urlDescription)" as NSString)
        workspaceOpen(url, configuration) { _, error in
            if let error {
                NSLog("%@", "OpenPets could not open action URL \(urlDescription): \(error.localizedDescription)" as NSString)
            } else {
                NSLog("%@", "OpenPets finished launching action URL: \(urlDescription)" as NSString)
            }
        }
    }

    static func open(_ url: URL) {
        OpenPetsActionURLOpener().open(url)
    }

    private static func defaultWorkspaceOpen(
        url: URL,
        configuration: NSWorkspace.OpenConfiguration,
        completion: @escaping Completion
    ) {
        NSWorkspace.shared.open(url, configuration: configuration) { application, error in
            completion(application, error)
        }
    }

    static func traceDescription(for url: URL) -> String {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        let scheme = components?.scheme ?? url.scheme ?? "unknown"
        let host = components?.host.map { "://\($0)" } ?? ""
        let path = components?.path.isEmpty == false ? components?.path ?? "" : ""
        let query = components?.query == nil ? "" : "?<redacted>"
        let fragment = components?.fragment == nil ? "" : "#<redacted>"

        return "\(scheme)\(host)\(path)\(query)\(fragment)"
    }
}
