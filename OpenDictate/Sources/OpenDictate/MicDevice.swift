import AVFoundation
import CoreAudio

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
}
