import Foundation
import OpenPetsKit

struct OpenPetsClaudeCodeQuotaReader: Sendable {
    var cacheFileURL: URL?
    var claudeConfigurationURLs: [URL]

    init(
        cacheFileURL: URL? = nil,
        claudeConfigurationURLs: [URL] = Self.defaultClaudeConfigurationURLs()
    ) {
        self.cacheFileURL = cacheFileURL
        self.claudeConfigurationURLs = claudeConfigurationURLs
    }

    func snapshot(now: Date = Date()) -> OpenPetsClaudeCodeQuotaSnapshot? {
        OpenPetsClaudeCodeQuotaCache.load(
            from: cacheFileURL ?? OpenPetsClaudeCodeQuotaCache.defaultCacheFileURL,
            now: now
        )
    }

    func hasClaudeConfiguration(fileManager: FileManager = .default) -> Bool {
        claudeConfigurationURLs.contains { fileManager.fileExists(atPath: $0.path) }
    }

    static func defaultClaudeConfigurationURLs(
        homeDirectoryURL: URL = FileManager.default.homeDirectoryForCurrentUser
    ) -> [URL] {
        [
            homeDirectoryURL.appendingPathComponent(".claude", isDirectory: true),
            homeDirectoryURL.appendingPathComponent(".claude.json")
        ]
    }
}

@MainActor
final class OpenPetsClaudeCodeSurfacePlugin {
    static let pluginID = "openpets.plugin.claude-code"

    private let reader: OpenPetsClaudeCodeQuotaReader
    private let refreshInterval: Duration
    private var updateHandler: (([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void)?
    private var task: Task<Void, Never>?

    init(reader: OpenPetsClaudeCodeQuotaReader = OpenPetsClaudeCodeQuotaReader(), refreshInterval: Duration = .seconds(30)) {
        self.reader = reader
        self.refreshInterval = refreshInterval
    }

    func start(updateHandler: @escaping ([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void) {
        self.updateHandler = updateHandler
        guard task == nil else {
            refresh()
            return
        }

        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                refresh()
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

    func refresh() {
        let now = Date()
        let snapshot = reader.snapshot()
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
                    OpenPetsSurfaceDetailRow(label: "Setup", value: "Use openpets claude-statusline")
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
            return "Unknown"
        }
        if delta > 0 {
            return "Over by \(delta)%"
        }
        if delta < 0 {
            return "Headroom \(abs(delta))%"
        }
        return "On pace"
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
