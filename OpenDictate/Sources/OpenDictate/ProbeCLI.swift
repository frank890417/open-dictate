import AppKit
import Foundation
import AVFoundation
import SwiftUI

/// Headless 驗證模式（不起 GUI），走的是「生產同一份」SocketClient / PCM16Resampler / WavWriter 程式碼。
///
///   OpenDictate --probe-ping                      # ping daemon
///   OpenDictate --probe-reload                    # reload_lexicon
///   OpenDictate --probe-transcribe <wav 路徑>     # 送 transcribe
///   OpenDictate --probe-wav <輸出路徑>            # 無麥克風：合成 48k 正弦波 → 轉檔管線 → 16k mono PCM16 wav
///   OpenDictate --probe-mic                       # 印出這次錄音會用哪支麥克風、為什麼（不錄音）
///   OpenDictate --probe-snapshot <png> [light|dark]  # 用今天的真實資料把控制中心畫成 PNG（不開視窗）
///   OpenDictate --probe-toggle                    # 叫正在跑的那一份打開／關上控制中心（可綁 Raycast／捷徑）
///   OpenDictate --probe-quiz [answer <id> yes|no] # 今天 3 題：印出題目（只看不記），或回答一題
///
/// exit code：0 = 拿到合法 daemon 回應（含 ok:false，如 no_speech）或 wav 產出成功；
///            2 = 傳輸層失敗（連不上 / timeout / 格式錯）；64 = 用法錯誤。
enum ProbeCLI {

    /// 有處理 → 回 exit code；nil → 正常走 GUI。
    static func run(arguments: [String]) -> Int32? {
        guard arguments.count >= 2, arguments[1].hasPrefix("--probe") else { return nil }
        let cmd = arguments[1]

        switch cmd {
        case "--probe-ping":
            return probeRoundTrip(.ping)
        case "--probe-reload":
            return probeRoundTrip(.reloadLexicon)
        case "--probe-stats":
            return probeRoundTrip(.stats)
        case "--probe-transcribe":
            guard arguments.count >= 3 else {
                FileHandle.standardError.write(Data("用法：--probe-transcribe <wav 路徑>\n".utf8))
                return 64
            }
            return probeRoundTrip(.transcribe(wavPath: arguments[2], punct: DictateSettings.punctMode))
        case "--probe-mic":
            let r = MicDevice.resolveRecordingDevice()
            print("lid_closed=\(MicDevice.isLidClosed())")
            print("preferred_uid=\(DictateSettings.preferredMicUID ?? "（系統預設）")")
            print("system_default=\(MicDevice.defaultInputDeviceID().flatMap(MicDevice.name(of:)) ?? "?")")
            print("will_use=\(r.name)" + (r.deviceID.map { " deviceID=\($0)" } ?? ""))
            print("note=\(r.note ?? "—")")
            return 0
        case "--probe-snapshot":
            guard arguments.count >= 3 else {
                FileHandle.standardError.write(Data("用法：--probe-snapshot <輸出 png> [light|dark]\n".utf8))
                return 64
            }
            let dark = arguments.count >= 4 && arguments[3] == "dark"
            return MainActor.assumeIsolated { probeSnapshot(outputPath: arguments[2], dark: dark) }
        case "--probe-toggle":
            DistributedNotificationCenter.default().postNotificationName(
                StatusItemController.toggleNotification, object: nil, userInfo: nil, deliverImmediately: true)
            print("posted \(StatusItemController.toggleNotification.rawValue)")
            return 0
        case "--probe-quiz":
            guard LexiconQuiz.isAvailable else { print("quiz=unavailable path=\(LexiconQuiz.scriptPath)"); return 0 }
            if arguments.count >= 5, arguments[2] == "answer" {
                let status = LexiconQuiz.answer(id: arguments[3], yes: arguments[4] == "yes")
                print("answer id=\(arguments[3]) status=\(status ?? "failed")")
                return status == nil ? 2 : 0
            }
            guard let snap = LexiconQuiz.today(peek: true) else { print("quiz=failed"); return 2 }
            print("pending_today=\(snap.pendingToday) pending_total=\(snap.pendingTotal) stale=\(snap.staleNotes)")
            for q in snap.questions { print("  \(q.id) [\(q.status)] \(q.wrong) → \(q.right)｜\(q.sample.prefix(30))") }
            return 0
        case "--probe-wav":
            guard arguments.count >= 3 else {
                FileHandle.standardError.write(Data("用法：--probe-wav <輸出 wav 路徑>\n".utf8))
                return 64
            }
            return probeWav(outputPath: arguments[2])
        default:
            FileHandle.standardError.write(Data("未知 probe 指令：\(cmd)\n".utf8))
            return 64
        }
    }

    private static func probeRoundTrip(_ request: DaemonRequest) -> Int32 {
        let client = SocketClient()
        do {
            let started = Date()
            let response = try client.roundTrip(request)
            let elapsed = Int(Date().timeIntervalSince(started) * 1000)
            print("raw: \(response.rawLine)")
            print("parsed: ok=\(response.ok)" +
                  (response.pong ? " pong=true model=\(response.model ?? "?") warm=\(response.warm.map(String.init) ?? "?")" : "") +
                  (response.text.map { " text=\"\($0)\"" } ?? "") +
                  (response.changes.map { " changes=\($0)" } ?? "") +
                  (response.error.map { " error=\($0)" } ?? "") +
                  " round_trip_ms=\(elapsed)")
            return 0
        } catch {
            let why = (error as? SocketClientError)?.description ?? error.localizedDescription
            FileHandle.standardError.write(Data("transport error: \(why)\n".utf8))
            return 2
        }
    }

    /// 模擬硬體格式（48kHz float32 non-interleaved）→ PCM16Resampler → WavWriter，
    /// 驗證錄音轉檔管線不需要真的開麥克風。
    private static func probeWav(outputPath: String) -> Int32 {
        guard let hwFormat = AVAudioFormat(standardFormatWithSampleRate: 48000, channels: 1) else {
            FileHandle.standardError.write(Data("hw format 建立失敗\n".utf8))
            return 2
        }
        guard let resampler = PCM16Resampler(inputFormat: hwFormat) else {
            FileHandle.standardError.write(Data("resampler 建立失敗\n".utf8))
            return 2
        }

        // 1.0s 的 440Hz 正弦波，分 10 個 chunk 餵（模擬 tap buffer 串流）
        let chunkFrames: AVAudioFrameCount = 4800
        for chunk in 0..<10 {
            guard let buf = AVAudioPCMBuffer(pcmFormat: hwFormat, frameCapacity: chunkFrames) else { return 2 }
            buf.frameLength = chunkFrames
            let samples = buf.floatChannelData![0]
            for i in 0..<Int(chunkFrames) {
                let t = Double(chunk * Int(chunkFrames) + i) / 48000.0
                samples[i] = Float(sin(2.0 * .pi * 440.0 * t) * 0.5)
            }
            resampler.append(buf)
        }
        let pcm = resampler.finish()

        do {
            try WavWriter.write(pcm16: pcm, to: URL(fileURLWithPath: outputPath))
        } catch {
            FileHandle.standardError.write(Data("wav 寫檔失敗：\(error)\n".utf8))
            return 2
        }
        let seconds = Double(pcm.count / 2) / 16000.0
        print("wav written: \(outputPath) pcm_bytes=\(pcm.count) duration=\(String(format: "%.3f", seconds))s (expect ~1.0s @16k mono PCM16)")
        return 0
    }

    @MainActor
    private static func probeSnapshot(outputPath: String, dark: Bool) -> Int32 {
        _ = NSApplication.shared
        let model = ControlCenterModel()
        model.refresh()
        for e in DictationStats.recentSuccessEntries(limit: 6).reversed() {
            model.push(.init(text: e.text, raw: e.raw, changes: e.changes, latencyMs: e.totalMs, at: e.date))
        }
        model.quiz = LexiconQuiz.today(peek: true)
        if let r = try? SocketClient(timeout: 2).roundTrip(.ping), r.ok {
            model.daemonOnline = true
            model.mode = r.mode
            model.daemonDetail = "線上（\(r.asrMain ?? r.model ?? "?")）"
        } else {
            model.daemonOnline = false
        }
        let view = ControlCenterView(model: model, solidBackground: true)
            .environment(\.colorScheme, dark ? .dark : .light)
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let cg = renderer.cgImage else {
            FileHandle.standardError.write(Data("畫不出來\n".utf8))
            return 2
        }
        let rep = NSBitmapImageRep(cgImage: cg)
        guard let png = rep.representation(using: .png, properties: [:]) else { return 2 }
        do {
            try png.write(to: URL(fileURLWithPath: outputPath))
            print("snapshot=\(outputPath) \(cg.width)x\(cg.height)")
            return 0
        } catch {
            FileHandle.standardError.write(Data("寫檔失敗：\(error)\n".utf8))
            return 2
        }
    }
}
