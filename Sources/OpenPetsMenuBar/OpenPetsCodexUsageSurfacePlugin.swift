import Foundation
import OpenPetsKit

struct OpenPetsCodexUsageBucket: Equatable, Sendable {
    var label: String
    var usedPercentage: Int
    var windowMinutes: Int?
    var resetDate: Date?
    var kind: String

    var remainingPercentage: Int {
        min(max(100 - usedPercentage, 0), 100)
    }
}

struct OpenPetsCodexUsageSnapshot: Equatable, Sendable {
    var planType: String?
    var primary: OpenPetsCodexUsageBucket?
    var secondary: OpenPetsCodexUsageBucket?
    var additional: OpenPetsCodexUsageBucket?
    var observedAt: Date
    var source: String
}

struct OpenPetsCodexUsageReader: Sendable {
    var authURL: URL
    var usageURL: URL

    init(
        codexHomeURL: URL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".codex", isDirectory: true),
        usageURL: URL = URL(string: "https://chatgpt.com/backend-api/wham/usage")!
    ) {
        self.authURL = codexHomeURL.appendingPathComponent("auth.json")
        self.usageURL = usageURL
    }

    init(authURL: URL, usageURL: URL = URL(string: "https://chatgpt.com/backend-api/wham/usage")!) {
        self.authURL = authURL
        self.usageURL = usageURL
    }

    func snapshot() async -> OpenPetsCodexUsageSnapshot? {
        await liveUsageSnapshot()
    }

    func hasCodexState(fileManager: FileManager = .default) -> Bool {
        fileManager.fileExists(atPath: authURL.path)
    }

    func liveUsageSnapshot() async -> OpenPetsCodexUsageSnapshot? {
        guard let accessToken = accessToken() else { return nil }
        var request = URLRequest(url: usageURL)
        request.httpMethod = "GET"
        request.timeoutInterval = 6
        request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("en-US", forHTTPHeaderField: "Accept-Language")
        request.setValue("Codex Desktop", forHTTPHeaderField: "originator")
        request.setValue("Codex Desktop/26.506.21252 (darwin; arm64)", forHTTPHeaderField: "User-Agent")
        if let accountID = Self.accountID(fromAccessToken: accessToken) {
            request.setValue(accountID, forHTTPHeaderField: "chatgpt-account-id")
        }

        guard
            let (data, response) = try? await URLSession.shared.data(for: request),
            let httpResponse = response as? HTTPURLResponse,
            (200..<300).contains(httpResponse.statusCode)
        else {
            return nil
        }

        return Self.snapshot(fromLiveUsageData: data, now: Date(), source: "live")
    }

    static func snapshot(
        fromLiveUsageData data: Data,
        now: Date = Date(),
        source: String = "live"
    ) -> OpenPetsCodexUsageSnapshot? {
        guard
            let payload = jsonObject(from: data),
            let rateLimits = (payload["limits"] as? [String: Any])
                ?? (payload["rate_limit"] as? [String: Any])
        else { return nil }

        return snapshot(fromRateLimits: rateLimits, planType: payload["plan_type"] as? String, now: now, source: source)
    }

    private static func snapshot(
        fromRateLimits rateLimits: [String: Any],
        planType: String?,
        now: Date,
        source: String
    ) -> OpenPetsCodexUsageSnapshot? {
        let primary = bucket(
            from: rateLimits["primary"] ?? rateLimits["primary_window"],
            kind: "primary",
            fallbackLabel: "5h",
            now: now
        )
        let secondary = bucket(
            from: rateLimits["secondary"] ?? rateLimits["secondary_window"],
            kind: "secondary",
            fallbackLabel: "7d",
            now: now
        )
        let additional = bucket(from: rateLimits["additional"], kind: "additional", fallbackLabel: "30d", now: now)
        guard primary != nil || secondary != nil || additional != nil else { return nil }

        return OpenPetsCodexUsageSnapshot(
            planType: planType,
            primary: primary,
            secondary: secondary,
            additional: additional,
            observedAt: now,
            source: source
        )
    }

    private func accessToken() -> String? {
        guard
            let data = try? Data(contentsOf: authURL),
            let payload = Self.jsonObject(from: data),
            let tokens = payload["tokens"] as? [String: Any],
            let token = tokens["access_token"] as? String,
            !token.isEmpty
        else {
            return nil
        }
        return token
    }

    private static func accountID(fromAccessToken token: String) -> String? {
        let parts = token.split(separator: ".")
        guard parts.count >= 2 else { return nil }
        var encodedPayload = String(parts[1])
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let padding = encodedPayload.count % 4
        if padding > 0 {
            encodedPayload += String(repeating: "=", count: 4 - padding)
        }
        guard
            let data = Data(base64Encoded: encodedPayload),
            let payload = jsonObject(from: data),
            let auth = payload["https://api.openai.com/auth"] as? [String: Any]
        else {
            return nil
        }
        return auth["chatgpt_account_id"] as? String
    }

    private static func jsonObject(from data: Data) -> [String: Any]? {
        if let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            return payload
        }
        guard
            let text = String(data: data, encoding: .utf8),
            let extracted = extractJSONObject(from: text),
            let extractedData = extracted.data(using: .utf8)
        else {
            return nil
        }
        return try? JSONSerialization.jsonObject(with: extractedData) as? [String: Any]
    }

    private static func extractJSONObject(from text: String) -> String? {
        guard let start = text.firstIndex(of: "{") else {
            return nil
        }
        var depth = 0
        var inString = false
        var escaping = false
        var index = start
        while index < text.endIndex {
            let character = text[index]
            if inString {
                if escaping {
                    escaping = false
                } else if character == "\\" {
                    escaping = true
                } else if character == "\"" {
                    inString = false
                }
            } else if character == "\"" {
                inString = true
            } else if character == "{" {
                depth += 1
            } else if character == "}" {
                depth -= 1
                if depth == 0 {
                    return String(text[start...index])
                }
            }
            index = text.index(after: index)
        }
        return nil
    }

    private static func bucket(
        from payload: Any?,
        kind: String,
        fallbackLabel: String,
        now: Date
    ) -> OpenPetsCodexUsageBucket? {
        guard
            let payload = payload as? [String: Any],
            let usedPercent = number(payload["used_percent"])
        else {
            return nil
        }

        let windowMinutes = number(payload["window_minutes"])
            ?? number(payload["limit_window_seconds"]).map { $0 / 60 }
        let resetSeconds = number(payload["resets_in_seconds"])
            ?? number(payload["reset_after_seconds"])
        let resetDate = resetSeconds.map { now.addingTimeInterval($0) }
            ?? number(payload["reset_at"]).map { Date(timeIntervalSince1970: $0) }

        return OpenPetsCodexUsageBucket(
            label: windowMinutes.map(formattedWindow(minutes:)) ?? fallbackLabel,
            usedPercentage: min(max(Int(usedPercent.rounded(.down)), 0), 100),
            windowMinutes: windowMinutes.map { Int($0.rounded()) },
            resetDate: resetDate,
            kind: kind
        )
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

    private static func formattedWindow(minutes: Double) -> String {
        let roundedMinutes = Int(minutes.rounded())
        if roundedMinutes >= 24 * 60 {
            return "\(roundedMinutes / (24 * 60))d"
        }
        if roundedMinutes >= 60 {
            return "\(roundedMinutes / 60)h"
        }
        return "\(roundedMinutes)m"
    }
}

@MainActor
final class OpenPetsCodexUsageSurfacePlugin {
    static let pluginID = "openpets.plugin.codex-usage"

    private let reader: OpenPetsCodexUsageReader
    private let refreshInterval: Duration
    private var updateHandler: (([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void)?
    private var task: Task<Void, Never>?

    init(reader: OpenPetsCodexUsageReader = OpenPetsCodexUsageReader(), refreshInterval: Duration = .seconds(60)) {
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
        let snapshot = await reader.snapshot()
        let surfaceUpdates = if let snapshot {
            Self.surfaceUpdates(for: snapshot, now: now)
        } else if reader.hasCodexState() {
            [Self.setupSurfaceUpdate()]
        } else {
            [OpenPetsSurfaceUpdate]()
        }
        updateHandler?(
            surfaceUpdates,
            Self.reactionUpdates(for: snapshot)
        )
    }

    nonisolated static func surfaceUpdates(
        for snapshot: OpenPetsCodexUsageSnapshot?,
        now: Date = Date()
    ) -> [OpenPetsSurfaceUpdate] {
        guard let snapshot else { return [] }
        var updates: [OpenPetsSurfaceUpdate] = []
        if let primary = snapshot.primary {
            updates.append(surfaceUpdate(
                for: primary,
                surfaceID: "codex.primary",
                slotPreference: [.hotspotTopLeading, .hotspotLeft],
                priorityBoost: 4,
                snapshot: snapshot,
                now: now
            ))
        }
        if let secondary = snapshot.secondary {
            updates.append(surfaceUpdate(
                for: secondary,
                surfaceID: "codex.secondary",
                slotPreference: [.hotspotBottomTrailing, .hotspotRight],
                priorityBoost: 0,
                snapshot: snapshot,
                now: now
            ))
        }
        if let additional = snapshot.additional {
            updates.append(surfaceUpdate(
                for: additional,
                surfaceID: "codex.additional",
                slotPreference: [.hotspotBottomLeading, .hotspotLeft],
                priorityBoost: -5,
                snapshot: snapshot,
                now: now
            ))
        }
        return updates
    }

    nonisolated static func setupSurfaceUpdate() -> OpenPetsSurfaceUpdate {
        OpenPetsSurfaceUpdate(
            surfaceID: "codex.usage.setup",
            slotPreference: [.hotspotBottomLeading, .hotspotLeft],
            priority: 5,
            icon: OpenPetsSurfaceIcons.info,
            value: "Codex",
            label: "Codex Usage",
            tone: .muted,
            detail: OpenPetsSurfaceDetailData(
                title: "Codex Usage",
                rows: [
                    OpenPetsSurfaceDetailRow(label: "Status", value: "Waiting for usage data"),
                    OpenPetsSurfaceDetailRow(label: "Source", value: "~/.codex/auth.json")
                ],
                ttlSeconds: 12
            )
        )
    }

    nonisolated static func reactionUpdates(for snapshot: OpenPetsCodexUsageSnapshot?) -> [OpenPetsPetReactionUpdate] {
        guard
            let snapshot,
            [snapshot.primary, snapshot.secondary, snapshot.additional]
                .compactMap({ $0 })
                .contains(where: { tone(for: $0) == .critical })
        else {
            return []
        }
        return [
            OpenPetsPetReactionUpdate(
                reactionID: "codex.usage-critical",
                kind: .alert,
                priority: 75,
                ttlSeconds: 20
            )
        ]
    }

    private nonisolated static func surfaceUpdate(
        for bucket: OpenPetsCodexUsageBucket,
        surfaceID: String,
        slotPreference: [OpenPetsSurfaceSlot],
        priorityBoost: Int,
        snapshot: OpenPetsCodexUsageSnapshot,
        now: Date
    ) -> OpenPetsSurfaceUpdate {
        let tone = tone(for: bucket)
        return OpenPetsSurfaceUpdate(
            surfaceID: surfaceID,
            slotPreference: slotPreference,
            priority: priority(for: tone) + priorityBoost,
            icon: OpenPetsSurfaceIcons.quota,
            value: "\(bucket.label) \(bucket.usedPercentage)%",
            label: "Codex \(bucket.label)",
            tone: tone,
            detail: detail(for: bucket, tone: tone, snapshot: snapshot, now: now)
        )
    }

    private nonisolated static func detail(
        for bucket: OpenPetsCodexUsageBucket,
        tone: OpenPetsSurfaceTone,
        snapshot: OpenPetsCodexUsageSnapshot,
        now: Date
    ) -> OpenPetsSurfaceDetailData {
        var rows = [
            OpenPetsSurfaceDetailRow(label: "Used", value: "\(bucket.usedPercentage)%", tone: tone),
            OpenPetsSurfaceDetailRow(label: "Remaining", value: "\(bucket.remainingPercentage)%"),
            OpenPetsSurfaceDetailRow(label: "Source", value: snapshot.source.capitalized)
        ]
        if let resetDate = bucket.resetDate {
            rows.insert(OpenPetsSurfaceDetailRow(label: "Reset", value: formattedReset(resetDate, now: now)), at: 2)
        }
        if let planType = snapshot.planType, !planType.isEmpty {
            rows.append(OpenPetsSurfaceDetailRow(label: "Plan", value: planType))
        }
        return OpenPetsSurfaceDetailData(title: "Codex \(bucket.label)", rows: rows, ttlSeconds: 12)
    }

    private nonisolated static func tone(for bucket: OpenPetsCodexUsageBucket) -> OpenPetsSurfaceTone {
        if bucket.usedPercentage >= 90 {
            return .critical
        }
        if bucket.usedPercentage >= 70 {
            return .warning
        }
        return .normal
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

    private nonisolated static func formattedReset(_ date: Date, now: Date = Date()) -> String {
        let minutes = max(0, Int(date.timeIntervalSince(now) / 60))
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
}
