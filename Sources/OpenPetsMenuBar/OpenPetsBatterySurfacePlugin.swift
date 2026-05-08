import Foundation
import IOKit.ps
import OpenPetsKit

struct OpenPetsBatterySnapshot: Equatable, Sendable {
    var percent: Int
    var isCharging: Bool
    var isPresent: Bool
    var timeRemainingMinutes: Int?
}

struct OpenPetsBatteryReader: Sendable {
    func snapshot() -> OpenPetsBatterySnapshot? {
        guard
            let info = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
            let sources = IOPSCopyPowerSourcesList(info)?.takeRetainedValue() as? [CFTypeRef]
        else {
            return nil
        }

        for source in sources {
            guard
                let description = IOPSGetPowerSourceDescription(info, source)?.takeUnretainedValue() as? [String: Any],
                let type = description[kIOPSTypeKey] as? String,
                type == kIOPSInternalBatteryType
            else {
                continue
            }

            let currentCapacity = description[kIOPSCurrentCapacityKey] as? Int
            let maxCapacity = description[kIOPSMaxCapacityKey] as? Int
            let percent: Int
            if let currentCapacity, let maxCapacity, maxCapacity > 0 {
                percent = Int((Double(currentCapacity) / Double(maxCapacity) * 100).rounded())
            } else if let capacity = description[kIOPSCurrentCapacityKey] as? Double {
                percent = Int(capacity.rounded())
            } else {
                continue
            }

            let state = description[kIOPSPowerSourceStateKey] as? String
            let isCharging = (description[kIOPSIsChargingKey] as? Bool) ?? (state == kIOPSACPowerValue)
            let isPresent = (description[kIOPSIsPresentKey] as? Bool) ?? true
            let timeRemaining = description[kIOPSTimeToEmptyKey] as? Int
            let timeRemainingMinutes = timeRemaining.flatMap { $0 >= 0 ? $0 : nil }

            return OpenPetsBatterySnapshot(
                percent: min(max(percent, 0), 100),
                isCharging: isCharging,
                isPresent: isPresent,
                timeRemainingMinutes: timeRemainingMinutes
            )
        }

        return nil
    }
}

@MainActor
final class OpenPetsBatterySurfacePlugin {
    static let pluginID = "openpets.plugin.battery"

    private let reader: OpenPetsBatteryReader
    private let refreshInterval: Duration
    private var updateHandler: (([OpenPetsSurfaceUpdate], [OpenPetsPetReactionUpdate]) -> Void)?
    private var task: Task<Void, Never>?

    init(reader: OpenPetsBatteryReader = OpenPetsBatteryReader(), refreshInterval: Duration = .seconds(60)) {
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
        let snapshot = reader.snapshot()
        updateHandler?(
            Self.surfaceUpdates(for: snapshot),
            Self.reactionUpdates(for: snapshot)
        )
    }

    nonisolated static func surfaceUpdates(for snapshot: OpenPetsBatterySnapshot?) -> [OpenPetsSurfaceUpdate] {
        guard let snapshot, snapshot.isPresent else { return [] }

        return [badgeUpdate(for: snapshot)]
    }

    private nonisolated static func badgeUpdate(for snapshot: OpenPetsBatterySnapshot) -> OpenPetsSurfaceUpdate {
        OpenPetsSurfaceUpdate(
            surfaceID: "battery.badge",
            slotPreference: [.hotspotTopTrailing, .hotspotRight],
            priority: tone(for: snapshot).priority,
            icon: symbol(for: snapshot),
            value: "\(snapshot.percent)%",
            label: "Battery",
            tone: tone(for: snapshot),
            detail: detail(for: snapshot)
        )
    }

    nonisolated static func reactionUpdates(for snapshot: OpenPetsBatterySnapshot?) -> [OpenPetsPetReactionUpdate] {
        guard let snapshot, snapshot.isPresent else { return [] }
        if snapshot.percent <= 10, !snapshot.isCharging {
            return [
                OpenPetsPetReactionUpdate(
                    reactionID: "battery.low-energy",
                    kind: .lowEnergy,
                    priority: 90
                )
            ]
        }
        if snapshot.isCharging {
            return [
                OpenPetsPetReactionUpdate(
                    reactionID: "battery.charging",
                    kind: .charging,
                    priority: 20
                )
            ]
        }
        return []
    }

    private nonisolated static func detail(for snapshot: OpenPetsBatterySnapshot) -> OpenPetsSurfaceDetailData {
        var rows = [
            OpenPetsSurfaceDetailRow(label: "Charge", value: "\(snapshot.percent)%", tone: tone(for: snapshot)),
            OpenPetsSurfaceDetailRow(label: "State", value: snapshot.isCharging ? "Charging" : "Battery")
        ]
        if let timeRemainingMinutes = snapshot.timeRemainingMinutes, !snapshot.isCharging {
            rows.append(OpenPetsSurfaceDetailRow(label: "Remaining", value: formattedDuration(minutes: timeRemainingMinutes)))
        }
        return OpenPetsSurfaceDetailData(title: "Battery", rows: rows)
    }

    private nonisolated static func tone(for snapshot: OpenPetsBatterySnapshot) -> OpenPetsSurfaceTone {
        if snapshot.isCharging {
            return .success
        }
        if snapshot.percent <= 10 {
            return .critical
        }
        if snapshot.percent <= 20 {
            return .warning
        }
        return .normal
    }

    private nonisolated static func symbol(for snapshot: OpenPetsBatterySnapshot) -> String {
        if snapshot.isCharging {
            return OpenPetsSurfaceIcons.batteryCharging
        }
        switch snapshot.percent {
        case 76...:
            return OpenPetsSurfaceIcons.battery100
        case 51...75:
            return OpenPetsSurfaceIcons.battery75
        case 26...50:
            return OpenPetsSurfaceIcons.battery50
        default:
            return OpenPetsSurfaceIcons.battery25
        }
    }

    private nonisolated static func formattedDuration(minutes: Int) -> String {
        guard minutes >= 60 else {
            return "\(minutes)m"
        }
        return "\(minutes / 60)h \(minutes % 60)m"
    }

}

private extension OpenPetsSurfaceTone {
    var priority: Int {
        switch self {
        case .critical:
            90
        case .warning:
            70
        case .success:
            20
        case .normal, .muted:
            10
        }
    }
}
