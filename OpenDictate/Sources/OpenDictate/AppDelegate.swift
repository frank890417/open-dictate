import AppKit
import Foundation

/// 狀態機：idle → recording → transcribing → 注入 → idle
final class AppDelegate: NSObject, NSApplicationDelegate {

    private enum State {
        case idle, recording, transcribing
    }

    private let statusController = StatusItemController()
    private let hotkeyMonitor = HotkeyMonitor()
    private let recorder = AudioRecorder()
    private let hud = RecordingHUD()
    private let settingsWindow = SettingsWindowController()
    private let socketQueue = DispatchQueue(label: "org.opendictate.socket", qos: .userInitiated)

    private var state: State = .idle
    private let minHoldSeconds = 0.5
    private var errorResetWorkItem: DispatchWorkItem?
    private var lastDaemonModel: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusController.setup()
        statusController.onReloadLexicon = { [weak self] in self?.reloadLexicon() }
        statusController.onCheckDaemon = { [weak self] in self?.pingDaemon() }
        statusController.onRestartDaemon = { [weak self] in self?.restartDaemon() }
        statusController.onReportMishear = { [weak self] wrong, right in self?.addLexiconPair(wrong: wrong, right: right) }
        statusController.onOpenSettings = { [weak self] in self?.openSettings() }
        statusController.onOpenLog = { [weak self] in self?.statusController.openTodayLog() }
        statusController.onHotkeyChanged = { [weak self] in self?.hotkeyMonitor.reconfigure() }

        settingsWindow.onReloadLexicon = { [weak self] in self?.reloadLexicon() }
        settingsWindow.onRestartDaemon = { [weak self] in self?.restartDaemon() }
        settingsWindow.onOpenLog = { [weak self] in self?.statusController.openTodayLog() }
        settingsWindow.onCheckDaemon = { [weak self] in self?.pingDaemon() }
        settingsWindow.onHotkeyChanged = { [weak self] in self?.hotkeyMonitor.reconfigure() }

        Notifier.setup()
        PermissionGuide.runStartupChecks()

        hotkeyMonitor.onDown = { [weak self] in self?.pttPressed() }
        hotkeyMonitor.onUp = { [weak self] in self?.pttReleased() }
        hotkeyMonitor.start()

        recorder.onLevel = { [weak self] level in self?.hud.updateLevel(level) }

        statusController.refreshStats()
        pingDaemon()
        mdLog("OpenDictate v0.5 啟動（熱鍵=\(DictateSettings.hotkeyLabel), AX=\(PermissionGuide.isAccessibilityTrusted)）")
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    private func openSettings() {
        settingsWindow.refreshStats()
        settingsWindow.show()
    }

    // MARK: - Push-to-talk

    private func pttPressed() {
        guard state == .idle else { return }
        do {
            try recorder.start()
            state = .recording
            statusController.setState(.recording)
            if DictateSettings.hudEnabled { hud.show(.recording) }
        } catch {
            mdLog("開錄失敗：\(error)")
            showError("開錄失敗", body: "\(error)")
            if case AudioRecorderError.micNotAuthorized = error {
                PermissionGuide.showGuideAlert()
            }
        }
    }

    private func pttReleased() {
        guard state == .recording else { return }
        guard let result = recorder.stop() else {
            backToIdle()
            return
        }

        guard result.wallSeconds >= minHoldSeconds, result.audioSeconds >= 0.2, !result.pcm16.isEmpty else {
            mdLog(String(format: "丟棄短錄音（wall %.2fs / audio %.2fs）", result.wallSeconds, result.audioSeconds))
            backToIdle()
            return
        }

        let wavPath = Self.makeWavPath()
        do {
            try WavWriter.write(pcm16: result.pcm16, to: URL(fileURLWithPath: wavPath))
        } catch {
            mdLog("wav 寫檔失敗：\(error)")
            showError("錄音檔寫入失敗", body: error.localizedDescription)
            return
        }

        state = .transcribing
        statusController.setState(.transcribing)
        if DictateSettings.hudEnabled { hud.show(.transcribing) }
        transcribe(wavPath: wavPath, audioSeconds: result.audioSeconds)
    }

    private static func makeWavPath() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let stamp = formatter.string(from: Date())
        let path = "/tmp/open-dictate-rec-\(stamp).wav"
        if FileManager.default.fileExists(atPath: path) {
            let ms = Int(Date().timeIntervalSince1970 * 1000) % 1000
            return "/tmp/open-dictate-rec-\(stamp)-\(String(format: "%03d", ms)).wav"
        }
        return path
    }

    // MARK: - Daemon

    /// IO-CONTRACT 停損：固定 10s 對長口述 + llm_zh fallback 太短——2026-07-16 事故，117s
    /// 音檔 + llm_zh 閘門驗證失敗回退 smart_zh，total 16.875s 超過原門檻，殼誤判 daemon 逾時/離線，
    /// 但轉錄其實成功（daemon 端 error_count 0）。改隨音檔長度 + llm_zh 最壞情況 headroom 動態抓。
    private static func transcribeTimeout(forAudioSeconds audioSeconds: Double) -> TimeInterval {
        let asrHeadroom = audioSeconds / 6  // 實測長音檔 ASR ≈ audio 的 1/13（117.4s→8.86s），抓 2x 安全係數
        let llmHeadroom: TimeInterval = 12  // llm_zh 全流程（PUNCT_LLM_TIMEOUT_S=8s + 閘門驗證）觀測 <8.1s，抓 buffer
        return max(15, asrHeadroom + llmHeadroom)
    }

    private func transcribe(wavPath: String, audioSeconds: Double) {
        let client = SocketClient(timeout: Self.transcribeTimeout(forAudioSeconds: audioSeconds))
        let punct = DictateSettings.punctMode
        socketQueue.async { [weak self] in
            let outcome: Result<DaemonResponse, Error> = Result {
                try client.roundTrip(.transcribe(wavPath: wavPath, punct: punct))
            }
            DispatchQueue.main.async {
                self?.handleTranscribeOutcome(outcome, wavPath: wavPath)
            }
        }
    }

    private func handleTranscribeOutcome(_ outcome: Result<DaemonResponse, Error>, wavPath: String) {
        switch outcome {
        case .success(let response):
            if response.ok, let text = response.text, !text.isEmpty {
                let latencyMs = response.totalMs.map { Int($0) }
                let changes = response.changes ?? []
                mdLog("轉錄 OK\(latencyMs.map { " (\($0)ms)" } ?? "")：\(text.prefix(80))")
                statusController.setSuccess(text: text, raw: response.raw, latencyMs: latencyMs, changes: changes)
                statusController.pushHistory(text: text, raw: response.raw)
                settingsWindow.refreshStats()

                if DictateSettings.hudEnabled {
                    hud.show(.success(text: text, raw: response.raw, latencyMs: latencyMs, changes: changes))
                } else {
                    hud.hide()
                }
                TextInjector.inject(text) { [weak self] method in
                    mdLog("注入完成：\(method.rawValue)")
                    self?.backToIdle(keepHUD: true)
                }
            } else if response.error == DaemonResponse.errorNoSpeech || (response.ok && (response.text ?? "").isEmpty) {
                mdLog("no_speech，安靜返回 idle")
                backToIdle()
            } else {
                let code = response.error ?? "unknown"
                showError("轉錄失敗", body: "daemon 回報：\(code)")
                cleanupWav(wavPath)
            }
        case .failure(let error):
            let why = (error as? SocketClientError)?.description ?? error.localizedDescription
            showError("daemon 沒有回應", body: "\(why)。daemon 起了嗎？（見 SETUP.md）")
            cleanupWav(wavPath)
            statusController.setDaemonStatus("連不上 ✗")
            settingsWindow.setDaemonStatus("連不上 ✗", model: lastDaemonModel)
        }
    }

    private func pingDaemon() {
        let client = SocketClient(timeout: 2)
        socketQueue.async { [weak self] in
            let outcome = Result { try client.roundTrip(.ping) }
            DispatchQueue.main.async {
                switch outcome {
                case .success(let r) where r.ok && r.pong:
                    let warm = (r.warm ?? false) ? "warm" : "cold"
                    let model = r.model?.split(separator: "/").last.map(String.init) ?? "?"
                    self?.lastDaemonModel = model
                    let ver = r.version.map { " v\($0)" } ?? ""
                    let status = "線上 ✓（\(model), \(warm)\(ver)）"
                    self?.statusController.setDaemonStatus(status)
                    self?.settingsWindow.setDaemonStatus(status, model: r.model)
                case .success:
                    self?.statusController.setDaemonStatus("回應異常")
                    self?.settingsWindow.setDaemonStatus("回應異常")
                case .failure:
                    self?.statusController.setDaemonStatus("離線 ✗")
                    self?.settingsWindow.setDaemonStatus("離線 ✗")
                }
            }
        }
    }

    private func reloadLexicon() {
        let client = SocketClient(timeout: 5)
        socketQueue.async { [weak self] in
            let outcome = Result { try client.roundTrip(.reloadLexicon) }
            DispatchQueue.main.async {
                switch outcome {
                case .success(let r) where r.ok:
                    let n = r.replacements.map { "\($0) 條" } ?? "完成"
                    Notifier.notify(title: "詞庫已重載", body: n)
                case .success(let r):
                    Notifier.notify(title: "詞庫重載失敗", body: r.error ?? "daemon 回報錯誤")
                case .failure(let e):
                    let why = (e as? SocketClientError)?.description ?? e.localizedDescription
                    Notifier.notify(title: "詞庫重載失敗", body: why)
                    self?.statusController.setDaemonStatus("離線 ✗")
                }
            }
        }
    }

    private func backToIdle(keepHUD: Bool = false) {
        errorResetWorkItem?.cancel()
        errorResetWorkItem = nil
        state = .idle
        statusController.setState(.idle)
        if !keepHUD { hud.hide() }
    }

    /// 寫入個人詞庫：優先 daemon add_pair（v0.5），失敗再 CLI fallback。
    private func addLexiconPair(wrong: String, right: String) {
        let client = SocketClient(timeout: 5)
        socketQueue.async { [weak self] in
            let outcome = Result {
                try client.roundTrip(.addPair(wrong: wrong, right: right, source: "dictate-ui"))
            }
            DispatchQueue.main.async {
                switch outcome {
                case .success(let r) where r.ok:
                    Notifier.notify(title: "詞庫已學會", body: "\(wrong) → \(right)")
                    self?.reloadLexicon()
                case .success(let r) where r.error == "unknown_cmd":
                    // 舊 daemon：CLI fallback
                    self?.addLexiconPairViaCLI(wrong: wrong, right: right)
                case .success(let r):
                    Notifier.notify(title: "詞庫寫入失敗", body: r.error ?? "未知錯誤")
                case .failure:
                    self?.addLexiconPairViaCLI(wrong: wrong, right: right)
                }
            }
        }
    }

    private func addLexiconPairViaCLI(wrong: String, right: String) {
        let museBotRoot = ProcessInfo.processInfo.environment["OPEN_DICTATE_LEXICON_ROOT"]
            ?? "\(Bundle.main.bundlePath)/Contents/Resources/vendor"
        let py = "\(museBotRoot)/tools/td-subtitle/.venv/bin/python3"
        let lexCli = "\(museBotRoot)/tools/muse-lexicon/muse_lexicon.py"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: FileManager.default.isExecutableFile(atPath: py) ? py : "/usr/bin/python3")
        p.arguments = [lexCli, "add", wrong, right, "--source", "dictate-ui"]
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        socketQueue.async { [weak self] in
            do {
                try p.run()
                p.waitUntilExit()
                let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                DispatchQueue.main.async {
                    if p.terminationStatus == 0 {
                        Notifier.notify(title: "詞庫已學會", body: "\(wrong) → \(right)")
                        self?.reloadLexicon()
                    } else {
                        Notifier.notify(title: "詞庫寫入失敗", body: String(out.prefix(120)))
                    }
                }
            } catch {
                DispatchQueue.main.async { Notifier.notify(title: "詞庫寫入失敗", body: "\(error)") }
            }
        }
    }

    private func restartDaemon() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = ["kickstart", "-k", "gui/\(getuid())/org.opendictate.daemon"]
        do {
            try p.run()
            p.waitUntilExit()
            statusController.setDaemonStatus("重啟中…（模型 warm ~6s）")
            settingsWindow.setDaemonStatus("重啟中…（模型 warm ~6s）")
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in self?.pingDaemon() }
        } catch {
            Notifier.notify(title: "daemon 重啟失敗", body: "\(error)")
        }
    }

    private func showError(_ title: String, body: String) {
        state = .idle
        statusController.setState(.error)
        statusController.setLastResult("⚠️ \(title)：\(body)")
        if DictateSettings.hudEnabled {
            hud.show(.error(message: title))
        } else {
            hud.hide()
        }
        Notifier.notify(title: title, body: body)

        errorResetWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, self.state == .idle else { return }
            self.statusController.setState(.idle)
        }
        errorResetWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
    }

    private func cleanupWav(_ path: String) {
        try? FileManager.default.removeItem(atPath: path)
    }
}
