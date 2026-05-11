import Foundation
import OpenPetsKit

struct OpenPetsClaudeCodeOAuthCredentials: Equatable, Sendable {
    var accessToken: String
    var expiresAt: Date?
}

private enum OpenPetsClaudeCodeUsageFetchResult: Sendable {
    case snapshot(OpenPetsClaudeCodeQuotaSnapshot)
    case unauthorized
    case failed
}

struct OpenPetsClaudeCodeQuotaReader: Sendable {
    typealias DataLoader = @Sendable (URLRequest) async throws -> (Data, URLResponse)

    var credentialsURL: URL
    var claudeConfigurationURLs: [URL]
    var usageURL: URL
    var processRunner: OpenPetsProcessRunning
    var shellURL: URL
    var environment: [String: String]
    var userAgent: String
    var dataLoader: DataLoader

    init(
        claudeHomeURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude", isDirectory: true),
        claudeConfigurationURLs: [URL] = Self.defaultClaudeConfigurationURLs(),
        usageURL: URL = URL(string: "https://api.anthropic.com/api/oauth/usage")!,
        processRunner: OpenPetsProcessRunning = OpenPetsDefaultProcessRunner(),
        shellURL: URL = URL(fileURLWithPath: "/bin/zsh"),
        environment: [String: String] = ProcessInfo.processInfo.environment,
        userAgent: String = "claude-code/1.0.0",
        dataLoader: @escaping DataLoader = { request in
            try await URLSession.shared.data(for: request)
        }
    ) {
        self.credentialsURL = claudeHomeURL.appendingPathComponent(".credentials.json")
        self.claudeConfigurationURLs = claudeConfigurationURLs
        self.usageURL = usageURL
        self.processRunner = processRunner
        self.shellURL = shellURL
        self.environment = environment
        self.userAgent = userAgent
        self.dataLoader = dataLoader
    }

    init(
        credentialsURL: URL,
        claudeConfigurationURLs: [URL]? = nil,
        usageURL: URL = URL(string: "https://api.anthropic.com/api/oauth/usage")!,
        processRunner: OpenPetsProcessRunning = OpenPetsDefaultProcessRunner(),
        shellURL: URL = URL(fileURLWithPath: "/bin/zsh"),
        environment: [String: String] = ProcessInfo.processInfo.environment,
        userAgent: String = "claude-code/1.0.0",
        dataLoader: @escaping DataLoader = { request in
            try await URLSession.shared.data(for: request)
        }
    ) {
        self.credentialsURL = credentialsURL
        self.claudeConfigurationURLs = claudeConfigurationURLs ?? [
            credentialsURL,
            credentialsURL.deletingLastPathComponent()
        ]
        self.usageURL = usageURL
        self.processRunner = processRunner
        self.shellURL = shellURL
        self.environment = environment
        self.userAgent = userAgent
        self.dataLoader = dataLoader
    }

    func snapshot(now: Date = Date()) async -> OpenPetsClaudeCodeQuotaSnapshot? {
        guard var credentials = credentials(now: now) else { return nil }
        if credentials.isExpired(now: now) {
            refreshClaudeCodeCredentials()
            guard
                let refreshedCredentials = self.credentials(now: now),
                !refreshedCredentials.isExpired(now: now)
            else {
                return nil
            }
            credentials = refreshedCredentials
        }

        switch await liveUsageSnapshot(accessToken: credentials.accessToken, now: now) {
        case let .snapshot(snapshot):
            return snapshot
        case .failed:
            return nil
        case .unauthorized:
            break
        }

        refreshClaudeCodeCredentials()
        guard
            let refreshedCredentials = self.credentials(now: Date()),
            refreshedCredentials.accessToken != credentials.accessToken || credentials.isExpired(now: Date())
        else {
            return nil
        }
        if case let .snapshot(snapshot) = await liveUsageSnapshot(accessToken: refreshedCredentials.accessToken, now: Date()) {
            return snapshot
        }
        return nil
    }

    func hasClaudeConfiguration(fileManager: FileManager = .default) -> Bool {
        if environment["CLAUDE_CODE_OAUTH_TOKEN"]?.isEmpty == false {
            return true
        }
        return claudeConfigurationURLs.contains { fileManager.fileExists(atPath: $0.path) }
    }

    static func defaultClaudeConfigurationURLs(
        homeDirectoryURL: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> [URL] {
        [
            homeDirectoryURL.appendingPathComponent(".claude", isDirectory: true),
            homeDirectoryURL.appendingPathComponent(".claude.json")
        ]
    }

    static func snapshot(
        fromOAuthUsageData data: Data,
        now: Date = Date()
    ) -> OpenPetsClaudeCodeQuotaSnapshot? {
        guard
            let payload = jsonObject(from: data),
            let fiveHour = payload["five_hour"] as? [String: Any],
            let sevenDay = payload["seven_day"] as? [String: Any],
            let fiveHourUsed = percentage(fiveHour["utilization"]),
            let sevenDayUsed = percentage(sevenDay["utilization"]),
            let fiveHourResetDate = resetDate(fiveHour["resets_at"]),
            let sevenDayResetDate = resetDate(sevenDay["resets_at"]),
            fiveHourResetDate > now,
            sevenDayResetDate > now
        else {
            return nil
        }

        return OpenPetsClaudeCodeQuotaSnapshot(
            fiveHour: OpenPetsClaudeCodeQuotaWindow(
                label: "5h",
                usedPercentage: fiveHourUsed,
                resetDate: fiveHourResetDate,
                durationMinutes: 5 * 60
            ),
            sevenDay: OpenPetsClaudeCodeQuotaWindow(
                label: "7d",
                usedPercentage: sevenDayUsed,
                resetDate: sevenDayResetDate,
                durationMinutes: 7 * 24 * 60
            )
        )
    }

    func credentials(now: Date = Date()) -> OpenPetsClaudeCodeOAuthCredentials? {
        if let credentials = keychainCredentials() {
            return credentials
        }
        if let credentials = fileCredentials(from: credentialsURL) {
            return credentials
        }
        if let token = environment["CLAUDE_CODE_OAUTH_TOKEN"], !token.isEmpty {
            return OpenPetsClaudeCodeOAuthCredentials(accessToken: token, expiresAt: nil)
        }
        return nil
    }

    private func liveUsageSnapshot(accessToken: String, now: Date) async -> OpenPetsClaudeCodeUsageFetchResult {
        var request = URLRequest(url: usageURL)
        request.httpMethod = "GET"
        request.timeoutInterval = 6
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("oauth-2025-04-20", forHTTPHeaderField: "anthropic-beta")
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        guard let (data, response) = try? await dataLoader(request),
              let httpResponse = response as? HTTPURLResponse
        else {
            return .failed
        }

        guard (200..<300).contains(httpResponse.statusCode) else {
            return httpResponse.statusCode == 401 ? .unauthorized : .failed
        }

        guard let snapshot = Self.snapshot(fromOAuthUsageData: data, now: now) else {
            return .failed
        }
        return .snapshot(snapshot)
    }

    private func keychainCredentials() -> OpenPetsClaudeCodeOAuthCredentials? {
        guard
            let result = try? processRunner.run(
                executableURL: URL(fileURLWithPath: "/usr/bin/security"),
                arguments: ["find-generic-password", "-s", "Claude Code-credentials", "-w"]
            ),
            result.succeeded,
            let data = result.standardOutput.data(using: .utf8)
        else {
            return nil
        }
        return Self.credentials(from: data)
    }

    private func fileCredentials(from url: URL) -> OpenPetsClaudeCodeOAuthCredentials? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return Self.credentials(from: data)
    }

    private static func credentials(from data: Data) -> OpenPetsClaudeCodeOAuthCredentials? {
        guard
            let payload = jsonObject(from: data),
            let oauth = payload["claudeAiOauth"] as? [String: Any],
            let accessToken = oauth["accessToken"] as? String,
            !accessToken.isEmpty
        else {
            return nil
        }
        let expiresAt = number(oauth["expiresAt"])
            .map { Date(timeIntervalSince1970: $0 / 1000) }
        return OpenPetsClaudeCodeOAuthCredentials(accessToken: accessToken, expiresAt: expiresAt)
    }

    private func refreshClaudeCodeCredentials() {
        guard
            let claudePathResult = runProcess(
                executableURL: shellURL,
                arguments: ["-lc", "command -v claude"],
                timeout: 3
            ),
            claudePathResult.succeeded,
            let claudePath = claudePathResult.standardOutput
                .split(whereSeparator: \.isNewline)
                .first
                .map(String.init),
            !claudePath.isEmpty
        else {
            return
        }

        let claudeURL = URL(fileURLWithPath: claudePath)
        if runProcess(executableURL: claudeURL, arguments: ["update"], timeout: 15)?.succeeded == true {
            return
        }
        _ = runProcess(executableURL: claudeURL, arguments: ["auth", "status"], timeout: 10)
    }

    private func runProcess(executableURL: URL, arguments: [String], timeout: TimeInterval) -> OpenPetsProcessResult? {
        if let defaultRunner = processRunner as? OpenPetsDefaultProcessRunner {
            return try? defaultRunner.run(executableURL: executableURL, arguments: arguments, timeout: timeout)
        }
        return try? processRunner.run(executableURL: executableURL, arguments: arguments)
    }

    private static func jsonObject(from data: Data) -> [String: Any]? {
        try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private static func percentage(_ value: Any?) -> Int? {
        guard let value = number(value) else { return nil }
        let percentage = Int(value.rounded(.down))
        guard (0...100).contains(percentage) else { return nil }
        return percentage
    }

    private static func resetDate(_ value: Any?) -> Date? {
        switch value {
        case let string as String:
            if let date = ISO8601DateFormatter().date(from: string) {
                return date
            }
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return formatter.date(from: string)
        case let int as Int:
            return Date(timeIntervalSince1970: TimeInterval(int))
        case let double as Double:
            return Date(timeIntervalSince1970: double)
        default:
            return nil
        }
    }

    private static func number(_ value: Any?) -> Double? {
        switch value {
        case let value as Double:
            value
        case let value as Int:
            Double(value)
        case let value as String:
            Double(value)
        default:
            nil
        }
    }
}

private extension OpenPetsClaudeCodeOAuthCredentials {
    func isExpired(now: Date) -> Bool {
        guard let expiresAt else { return false }
        return expiresAt.timeIntervalSince(now) <= 60
    }
}

@MainActor
final class OpenPetsClaudeCodeSurfacePlugin {
    static let pluginID = "openpets.plugin.claude-code"

    private let reader: OpenPetsClaudeCodeQuotaReader
    private let refreshInterval: Duration
    private var updateHandler: (([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void)?
    private var task: Task<Void, Never>?

    init(reader: OpenPetsClaudeCodeQuotaReader = OpenPetsClaudeCodeQuotaReader(), refreshInterval: Duration = .seconds(180)) {
        self.reader = reader
        self.refreshInterval = refreshInterval
    }

    func start(updateHandler: @escaping ([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void) {
        self.updateHandler = updateHandler
        guard task == nil else {
            Task { [weak self] in
                await self?.refresh()
            }
            return
        }

        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                await refresh()
                do {
                    try await ContinuousClock().sleep(for: refreshInterval)
                } catch {
                    return
                }
            }
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        updateHandler?([], [])
        updateHandler = nil
    }

    func refresh() async {
        let now = Date()
        let snapshot = await reader.snapshot(now: now)
        let surfaceUpdates = if let snapshot {
            Self.surfaceUpdates(for: snapshot, now: now)
        } else if reader.hasClaudeConfiguration() {
            [Self.setupSurfaceUpdate()]
        } else {
            [OpenPetsSurfaceUpdate]()
        }
        updateHandler?(
            surfaceUpdates,
            Self.reactionUpdates(for: snapshot, now: now)
        )
    }

    nonisolated static func surfaceUpdates(
        for snapshot: OpenPetsClaudeCodeQuotaSnapshot?,
        now: Date = Date()
    ) -> [OpenPetsSurfaceUpdate] {
        guard let snapshot else { return [] }

        return [
            surfaceUpdate(
                for: snapshot.fiveHour,
                surfaceID: "claude.5h",
                slotPreference: [.hotspotTopLeading, .hotspotLeft],
                priorityBoost: 5,
                now: now
            ),
            surfaceUpdate(
                for: snapshot.sevenDay,
                surfaceID: "claude.7d",
                slotPreference: [.hotspotBottomLeading, .hotspotLeft],
                priorityBoost: 0,
                now: now
            )
        ]
    }

    nonisolated static func setupSurfaceUpdate() -> OpenPetsSurfaceUpdate {
        OpenPetsSurfaceUpdate(
            surfaceID: "claude.setup",
            slotPreference: [.hotspotBottomLeading, .hotspotLeft],
            priority: 5,
            icon: OpenPetsSurfaceIcons.info,
            value: "Claude",
            label: "Claude Code",
            tone: .muted,
            detail: OpenPetsSurfaceDetailData(
                title: "Claude Code",
                rows: [
                    OpenPetsSurfaceDetailRow(label: "Status", value: "Waiting for quota data"),
                    OpenPetsSurfaceDetailRow(label: "Source", value: "Claude Code OAuth")
                ],
                actionURL: "https://github.com/alterhq/openpets/blob/main/docs/ai-assistants/claude-code.md",
                actionLabel: "Docs",
                ttlSeconds: 12
            )
        )
    }

    nonisolated static func reactionUpdates(
        for snapshot: OpenPetsClaudeCodeQuotaSnapshot?,
        now: Date = Date()
    ) -> [OpenPetsPetReactionUpdate] {
        guard let snapshot else { return [] }
        let windows = [snapshot.fiveHour, snapshot.sevenDay]
        guard windows.contains(where: { tone(for: $0, now: now) == .critical }) else {
            return []
        }
        return [
            OpenPetsPetReactionUpdate(
                reactionID: "claude.quota-critical",
                kind: .alert,
                priority: 80,
                ttlSeconds: 20
            )
        ]
    }

    private nonisolated static func surfaceUpdate(
        for window: OpenPetsClaudeCodeQuotaWindow,
        surfaceID: String,
        slotPreference: [OpenPetsSurfaceSlot],
        priorityBoost: Int,
        now: Date
    ) -> OpenPetsSurfaceUpdate {
        let tone = tone(for: window, now: now)
        return OpenPetsSurfaceUpdate(
            surfaceID: surfaceID,
            slotPreference: slotPreference,
            priority: priority(for: tone) + priorityBoost,
            icon: OpenPetsSurfaceIcons.quota,
            value: "\(window.label) \(window.usedPercentage)%",
            label: "Claude \(window.label)",
            tone: tone,
            detail: detail(for: window, tone: tone, now: now)
        )
    }

    private nonisolated static func detail(
        for window: OpenPetsClaudeCodeQuotaWindow,
        tone: OpenPetsSurfaceTone,
        now: Date
    ) -> OpenPetsSurfaceDetailData {
        let resetMinutes = minutesUntil(window.resetDate, now: now)
        return OpenPetsSurfaceDetailData(
            title: "Claude \(window.label)",
            rows: [
                OpenPetsSurfaceDetailRow(label: "Used", value: "\(window.usedPercentage)%", tone: tone),
                OpenPetsSurfaceDetailRow(label: "Reset", value: formattedDuration(minutes: resetMinutes)),
                OpenPetsSurfaceDetailRow(label: "Pace", value: paceDescription(for: window, now: now))
            ],
            ttlSeconds: 12
        )
    }

    private nonisolated static func tone(
        for window: OpenPetsClaudeCodeQuotaWindow,
        now: Date
    ) -> OpenPetsSurfaceTone {
        if window.usedPercentage >= 90 {
            return .critical
        }
        if window.usedPercentage >= 70 || (paceDelta(for: window, now: now) ?? 0) >= 15 {
            return .warning
        }
        return .normal
    }

    private nonisolated static func paceDescription(
        for window: OpenPetsClaudeCodeQuotaWindow,
        now: Date
    ) -> String {
        guard let delta = paceDelta(for: window, now: now) else {
            return "Pace unknown"
        }
        if delta > 0 {
            return "\(delta)% over target"
        }
        if delta < 0 {
            return "\(abs(delta))% under target"
        }
        return "On track"
    }

    private nonisolated static func paceDelta(
        for window: OpenPetsClaudeCodeQuotaWindow,
        now: Date
    ) -> Int? {
        let remainingMinutes = minutesUntil(window.resetDate, now: now)
        guard remainingMinutes <= window.durationMinutes else { return nil }
        let elapsedMinutes = max(0, window.durationMinutes - remainingMinutes)
        return window.usedPercentage - (elapsedMinutes * 100 / window.durationMinutes)
    }

    private nonisolated static func minutesUntil(_ date: Date, now: Date) -> Int {
        max(0, Int(date.timeIntervalSince(now) / 60))
    }

    private nonisolated static func formattedDuration(minutes: Int) -> String {
        if minutes >= 24 * 60 {
            return "\(minutes / (24 * 60))d"
        }
        guard minutes >= 60 else {
            return "\(minutes)m"
        }
        let hours = minutes / 60
        let remainingMinutes = minutes % 60
        guard remainingMinutes > 0 else {
            return "\(hours)h"
        }
        return "\(hours)h \(remainingMinutes)m"
    }

    private nonisolated static func priority(for tone: OpenPetsSurfaceTone) -> Int {
        switch tone {
        case .critical:
            90
        case .warning:
            70
        case .success:
            20
        case .normal, .muted:
            30
        }
    }
}
