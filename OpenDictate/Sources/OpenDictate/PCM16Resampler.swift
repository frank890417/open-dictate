import Foundation
import AVFoundation

/// 硬體格式（通常 48kHz float32）→ 16kHz mono PCM16 的串流轉換器。
/// IO-CONTRACT.md §音訊格式：殼負責轉好才交給 daemon。
///
/// 非 thread-safe：呼叫端（AudioRecorder 的 serial queue / probe CLI）自己管執行緒。
final class PCM16Resampler {
    static let targetFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 16000,
        channels: 1,
        interleaved: true
    )!

    private let converter: AVAudioConverter
    private(set) var pcmData = Data()

    /// 轉出的音長（秒）
    var outputSeconds: Double {
        Double(pcmData.count / 2) / Self.targetFormat.sampleRate
    }

    init?(inputFormat: AVAudioFormat) {
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0,
              let conv = AVAudioConverter(from: inputFormat, to: Self.targetFormat) else {
            return nil
        }
        // 多聲道 → mono 用預設 downmix
        conv.downmix = true
        converter = conv
    }

    /// 餵一個 tap buffer（串流式；converter 內部保留 resampler 狀態）。
    func append(_ buffer: AVAudioPCMBuffer) {
        guard buffer.frameLength > 0 else { return }
        let ratio = Self.targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: Self.targetFormat, frameCapacity: capacity) else { return }

        var fed = false
        var convError: NSError?
        let status = converter.convert(to: out, error: &convError) { _, outStatus in
            if fed {
                outStatus.pointee = .noDataNow
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return buffer
        }
        if status == .error {
            mdLog("resampler convert error: \(convError?.localizedDescription ?? "?")")
            return
        }
        appendFrames(from: out)
    }

    /// 收尾：把 resampler 內部殘留 frames 吐乾淨，回傳完整 PCM16 bytes。
    func finish() -> Data {
        for _ in 0..<16 { // 安全上限，實際 1-2 輪就乾
            guard let out = AVAudioPCMBuffer(pcmFormat: Self.targetFormat, frameCapacity: 4096) else { break }
            var convError: NSError?
            let status = converter.convert(to: out, error: &convError) { _, outStatus in
                outStatus.pointee = .endOfStream
                return nil
            }
            appendFrames(from: out)
            if status != .haveData || out.frameLength == 0 { break }
        }
        return pcmData
    }

    private func appendFrames(from buffer: AVAudioPCMBuffer) {
        guard buffer.frameLength > 0, let ch = buffer.int16ChannelData else { return }
        // target 是 interleaved mono → ch[0] 連續 frameLength 個 Int16
        let byteCount = Int(buffer.frameLength) * MemoryLayout<Int16>.size
        pcmData.append(Data(bytes: ch[0], count: byteCount))
    }
}
