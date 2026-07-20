import Foundation
import AVFoundation
import AudioToolbox
import CoreAudio

/// 一次錄音的結果
struct RecordingResult {
    /// 16kHz mono PCM16 raw bytes
    let pcm16: Data
    /// 按住熱鍵的牆鐘時間（防誤觸門檻用這個）
    let wallSeconds: Double
    /// 實際收到的音訊長度
    let audioSeconds: Double
}

enum AudioRecorderError: Error, CustomStringConvertible {
    case alreadyRecording
    case noInputDevice
    case converterInitFailed
    case engineStartFailed(String)
    case micNotAuthorized

    var description: String {
        switch self {
        case .alreadyRecording: return "已在錄音中"
        case .noInputDevice: return "找不到輸入裝置（麥克風）"
        case .converterInitFailed: return "音訊轉換器初始化失敗"
        case .engineStartFailed(let why): return "音訊引擎啟動失敗：\(why)"
        case .micNotAuthorized: return "麥克風權限未授權"
        }
    }
}

/// AVAudioEngine inputNode tap → PCM16Resampler → 記憶體累積。
/// 每次錄音用全新 engine；可指定 preferredMicUID（DictateSettings）。
final class AudioRecorder {
    private let queue = DispatchQueue(label: "org.opendictate.audio")
    private var engine: AVAudioEngine?
    private var resampler: PCM16Resampler?
    private var startedAt: Date?

    /// 即時音量回呼（main thread；0.0-1.0，給 HUD 波形用）。
    var onLevel: ((Float) -> Void)?

    var isRecording: Bool { engine != nil }

    /// 開錄。main thread 呼叫。
    func start() throws {
        guard engine == nil else { throw AudioRecorderError.alreadyRecording }
        guard AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            throw AudioRecorderError.micNotAuthorized
        }

        let newEngine = AVAudioEngine()

        // 偏好麥克風（系統預設則跳過）
        if let uid = DictateSettings.preferredMicUID,
           let deviceID = MicDevice.audioDeviceID(forUID: uid) {
            if Self.setInputDevice(newEngine.inputNode, deviceID: deviceID) {
                mdLog("使用指定麥克風 deviceID=\(deviceID) uid=\(uid.prefix(12))…")
            } else {
                mdLog("⚠️ 切換麥克風失敗，改用系統預設")
            }
        }

        let input = newEngine.inputNode
        let hwFormat = input.outputFormat(forBus: 0)
        guard hwFormat.sampleRate > 0, hwFormat.channelCount > 0 else {
            throw AudioRecorderError.noInputDevice
        }
        guard let newResampler = PCM16Resampler(inputFormat: hwFormat) else {
            throw AudioRecorderError.converterInitFailed
        }

        input.installTap(onBus: 0, bufferSize: 4096, format: hwFormat) { [weak self] buffer, _ in
            self?.queue.async { [weak self] in
                self?.resampler?.append(buffer)
            }
            if let onLevel = self?.onLevel,
               let data = buffer.floatChannelData?[0] {
                let n = Int(buffer.frameLength)
                guard n > 0 else { return }
                var acc: Float = 0
                for i in 0..<n { acc += data[i] * data[i] }
                let rms = (acc / Float(n)).squareRoot()
                let level = min(1.0, rms * 9)
                DispatchQueue.main.async { onLevel(level) }
            }
        }

        newEngine.prepare()
        do {
            try newEngine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw AudioRecorderError.engineStartFailed(error.localizedDescription)
        }

        queue.sync { self.resampler = newResampler }
        engine = newEngine
        startedAt = Date()
        mdLog("錄音開始（hw: \(Int(hwFormat.sampleRate))Hz \(hwFormat.channelCount)ch）")
    }

    func stop() -> RecordingResult? {
        guard let currentEngine = engine else { return nil }
        let wall = startedAt.map { Date().timeIntervalSince($0) } ?? 0

        currentEngine.inputNode.removeTap(onBus: 0)
        currentEngine.stop()
        engine = nil
        startedAt = nil

        var pcm = Data()
        var audioSeconds = 0.0
        queue.sync {
            if let r = self.resampler {
                pcm = r.finish()
                audioSeconds = r.outputSeconds
            }
            self.resampler = nil
        }
        mdLog(String(format: "錄音結束：wall %.2fs, audio %.2fs, %d bytes", wall, audioSeconds, pcm.count))
        return RecordingResult(pcm16: pcm, wallSeconds: wall, audioSeconds: audioSeconds)
    }

    func abort() {
        _ = stop()
    }

    /// 透過 AudioUnit 指定輸入裝置（AVAudioEngine 在 macOS 的標準做法）。
    private static func setInputDevice(_ inputNode: AVAudioInputNode, deviceID: AudioDeviceID) -> Bool {
        guard let audioUnit = inputNode.audioUnit else { return false }
        var id = deviceID
        let size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let status = AudioUnitSetProperty(
            audioUnit,
            kAudioOutputUnitProperty_CurrentDevice,
            kAudioUnitScope_Global,
            0,
            &id,
            size
        )
        return status == noErr
    }
}
