import AVFoundation
import CoreAudio
import IOKit

/// 本機音訊輸入裝置列舉與選擇（給設定窗 / 錄音器用）。
struct MicDevice: Equatable {
    let uid: String
    let name: String
    let isDefault: Bool

    /// 列出所有輸入裝置（含系統預設標記）。
    static func listInputs() -> [MicDevice] {
        var result: [MicDevice] = []
        let defaultUID = defaultInputUID()

        // AVCaptureDevice 列舉（使用者可讀名稱）
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.microphone, .external],
            mediaType: .audio,
            position: .unspecified
        )
        var seen = Set<String>()
        for dev in discovery.devices {
            let uid = dev.uniqueID
            guard !seen.contains(uid) else { continue }
            seen.insert(uid)
            result.append(MicDevice(
                uid: uid,
                name: dev.localizedName,
                isDefault: uid == defaultUID
            ))
        }

        // 若 AV 沒列到但 CoreAudio 有 default，至少放一筆
        if result.isEmpty, let uid = defaultUID {
            result.append(MicDevice(uid: uid, name: "系統預設麥克風", isDefault: true))
        }
        return result.sorted { a, b in
            if a.isDefault != b.isDefault { return a.isDefault }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    static func defaultInputUID() -> String? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &deviceID)
        guard status == noErr, deviceID != 0 else { return nil }
        return uid(for: deviceID)
    }

    static func audioDeviceID(forUID uid: String) -> AudioDeviceID? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var dataSize: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &dataSize) == noErr else {
            return nil
        }
        let count = Int(dataSize) / MemoryLayout<AudioDeviceID>.size
        var devices = [AudioDeviceID](repeating: 0, count: count)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &dataSize, &devices) == noErr else {
            return nil
        }
        for id in devices {
            if self.uid(for: id) == uid { return id }
        }
        return nil
    }

    private static func uid(for deviceID: AudioDeviceID) -> String? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cfUID: CFString? = nil
        var size = UInt32(MemoryLayout<CFString?>.size)
        let status = withUnsafeMutablePointer(to: &cfUID) { ptr in
            AudioObjectGetPropertyData(deviceID, &addr, 0, nil, &size, ptr)
        }
        guard status == noErr, let cfUID else { return nil }
        return cfUID as String
    }

    /// 顯示名稱：設定裡的偏好，或「系統預設」
    static func preferredDisplayName() -> String {
        guard let uid = DictateSettings.preferredMicUID else { return "系統預設" }
        return listInputs().first(where: { $0.uid == uid })?.name ?? "系統預設"
    }

    // MARK: - 錄音裝置決定（2026-10-07）
    //
    // 事故：指定的麥克風（AG06）沒接 → 退回系統預設＝筆電內建麥克風；筆電闔蓋時內建麥克風被靜音，
    // 錄到 -91 dB 平線，daemon 能量閘門判 no_speech，殼安靜回 idle，使用者只看到「失敗 4」。
    // ⇒ 闔蓋時不用內建麥克風，改用另一支實體輸入；真的沒有別支才留著它（錄完會被靜音偵測擋下來提示）。

    struct Resolved {
        let deviceID: AudioDeviceID?
        let name: String
        /// 有換裝置時的說明（寫進殼 log）
        let note: String?
    }

    static func resolveRecordingDevice() -> Resolved {
        var candidate: AudioDeviceID?
        var note: String?
        if let uid = DictateSettings.preferredMicUID {
            candidate = audioDeviceID(forUID: uid)
            if candidate == nil { note = "指定麥克風不在（\(uid.prefix(24))…），改用系統預設" }
        }
        if candidate == nil { candidate = defaultInputDeviceID() }
        guard let picked = candidate else { return Resolved(deviceID: nil, name: "系統預設麥克風", note: note) }

        if transportType(of: picked) == kAudioDeviceTransportTypeBuiltIn, isLidClosed() {
            if let alt = firstPhysicalInput(excluding: picked) {
                let why = "筆電闔蓋，內建麥克風收不到聲音，改用「\(name(of: alt) ?? "?")」"
                return Resolved(deviceID: alt, name: name(of: alt) ?? "?", note: [note, why].compactMap { $0 }.joined(separator: "；"))
            }
            note = [note, "筆電闔蓋，但找不到其他實體麥克風"].compactMap { $0 }.joined(separator: "；")
        }
        return Resolved(deviceID: picked, name: name(of: picked) ?? "系統預設麥克風", note: note)
    }

    /// 筆電螢幕是否闔上（IOPMrootDomain 的 AppleClamshellState；桌機沒有這個屬性 → false）。
    static func isLidClosed() -> Bool {
        let service = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOPMrootDomain"))
        guard service != 0 else { return false }
        defer { IOObjectRelease(service) }
        let value = IORegistryEntryCreateCFProperty(service, "AppleClamshellState" as CFString, kCFAllocatorDefault, 0)?
            .takeRetainedValue()
        return (value as? Bool) ?? false
    }

    static func defaultInputDeviceID() -> AudioDeviceID? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &deviceID)
        return status == noErr && deviceID != 0 ? deviceID : nil
    }

    static func name(of deviceID: AudioDeviceID) -> String? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cfName: CFString? = nil
        var size = UInt32(MemoryLayout<CFString?>.size)
        let status = withUnsafeMutablePointer(to: &cfName) { ptr in
            AudioObjectGetPropertyData(deviceID, &addr, 0, nil, &size, ptr)
        }
        guard status == noErr, let cfName else { return nil }
        return cfName as String
    }

    private static func transportType(of deviceID: AudioDeviceID) -> UInt32? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var t: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        return AudioObjectGetPropertyData(deviceID, &addr, 0, nil, &size, &t) == noErr ? t : nil
    }

    private static func hasInputStreams(_ deviceID: AudioDeviceID) -> Bool {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams,
            mScope: kAudioDevicePropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        return AudioObjectGetPropertyDataSize(deviceID, &addr, 0, nil, &size) == noErr && size > 0
    }

    /// 另一支實體輸入：排除內建、虛擬（BlackHole／NDI／Zoom…）、聚合裝置；USB 優先，其次依名稱排序。
    private static func firstPhysicalInput(excluding excluded: AudioDeviceID) -> AudioDeviceID? {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var dataSize: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &dataSize) == noErr else { return nil }
        var devices = [AudioDeviceID](repeating: 0, count: Int(dataSize) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &dataSize, &devices) == noErr else { return nil }

        let skip: Set<UInt32> = [kAudioDeviceTransportTypeBuiltIn, kAudioDeviceTransportTypeVirtual,
                                 kAudioDeviceTransportTypeAggregate, kAudioDeviceTransportTypeAutoAggregate,
                                 kAudioDeviceTransportTypeUnknown]
        let physical = devices.filter { id in
            guard id != excluded, hasInputStreams(id), let t = transportType(of: id) else { return false }
            return !skip.contains(t)
        }
        return physical.sorted { a, b in
            let ua = transportType(of: a) == kAudioDeviceTransportTypeUSB
            let ub = transportType(of: b) == kAudioDeviceTransportTypeUSB
            if ua != ub { return ua }
            return (name(of: a) ?? "").localizedCaseInsensitiveCompare(name(of: b) ?? "") == .orderedAscending
        }.first
    }
}
