import AppKit
import Foundation

/// 狀態機（v0.9 連講）：錄音一次一句；轉錄排隊。
///
///   按住熱鍵 → 錄音 → 放開 → 排進轉錄佇列 → 一次送 daemon 一句（照講話順序）→ 結果照順序打字
///
/// - 轉錄中也能開始錄下一句（v0.8 會「這次忽略」）；Cmd-V 一送出就回閒置（v0.8 每句再鎖 0.54 秒）。
/// - daemon 慢了不放棄：過了殼的逾時只是改顯示「還在轉…」，連線斷了就讀 daemon 的最後結果檔，
///   daemon 重啟前沒讀到的錄音重送一次。錄音檔殼不刪（daemon 處理完自己刪）。
/// - 晚到的句子：焦點換了、放開後又打了字、或後面那句已經先打出去 → 不插進文件，放剪貼簿＋最近紀錄＋控制中心一張卡。
final class AppDelegate: NSObject, NSApplicationDelegate {

    /// 一句送出去、還沒拿到結果的錄音
    private struct Job {
        let id: Int
        let wavPath: String
        let audioSeconds: Double
        let releasedAt: Date
        /// 放開熱鍵時在前景的 app：晚到的結果只在焦點沒換時才打字
        let targetPID: pid_t?
        /// 等超過殼的逾時了（「還在轉…」）
        var slow = false
        /// daemon 重啟後重送過一次
        var resent = false
    }

    private let statusController = StatusItemController()
    private let hotkeyMonitor = HotkeyMonitor()
    private let recorder = AudioRecorder()
    private let hud = RecordingHUD()
    private let settingsWindow = SettingsWindowController()
    /// 跟 daemon 的往返全部排在這條序列上：daemon 一次處理一句，殼也一次送一句，逾時才從真的開始處理時算
    private let socketQueue = DispatchQueue(label: "\(ProductConfig.shellLaunchLabel).socket", qos: .userInitiated)

    private var isRecording = false
    /// 送出、還沒回來的句子（講話順序）
    private var jobs: [Job] = []
    private var nextJobID = 1
    /// 最後打進去（或放剪貼簿）的是第幾句：比它早的晚到結果不插隊打字
    private var lastDeliveredJobID = 0
    private let minHoldSeconds = 0.5
    private var errorResetWorkItem: DispatchWorkItem?
    private var lastDaemonModel: String?
    private var recentAudioSeconds: Double?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusController.setup()
        statusController.onReloadLexicon = { [weak self] in self?.reloadLexicon() }
        statusController.onCheckDaemon = { [weak self] in self?.pingDaemon() }
        statusController.onRestartDaemon = { [weak self] in self?.restartDaemon() }
        statusController.onReportMishear = { [weak self] wrong, right in self?.addLexiconPair(wrong: wrong, right: right) }
        statusController.onSetAsrMode = { [weak self] mode in self?.setAsrMode(mode) }
        statusController.onOpenSettings = { [weak self] in self?.openSettings() }
        statusController.onOpenLog = { [weak self] in self?.statusController.openTodayLog() }
        statusController.onHotkeyChanged = { [weak self] in self?.hotkeyMonitor.reconfigure() }
        statusController.onRetranscribe = { [weak self] u in self?.retranscribe(u) }
        statusController.onWithdraw = { [weak self] u in self?.withdraw(u) }

        settingsWindow.onReloadLexicon = { [weak self] in self?.reloadLexicon() }
        settingsWindow.onRestartDaemon = { [weak self] in self?.restartDaemon() }
        settingsWindow.onOpenLog = { [weak self] in self?.statusController.openTodayLog() }
        settingsWindow.onCheckDaemon = { [weak self] in self?.pingDaemon() }
        settingsWindow.onHotkeyChanged = { [weak self] in self?.hotkeyMonitor.reconfigure() }

        Notifier.setup()
        PermissionGuide.runStartupChecks()

        hotkeyMonitor.onDown = { [weak self] in self?.pttPressed() }
        hotkeyMonitor.onUp = { [weak self] in self?.pttReleased() }
        hotkeyMonitor.onCancel = { [weak self] in self?.pttCancelled() }
        hotkeyMonitor.start()
        InsertionTracker.shared.start()

        recorder.onLevel = { [weak self] level in self?.hud.updateLevel(level) }

        statusController.refreshStats()
        pingDaemon()
        mdLog("\(ProductConfig.appName) v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "?") 啟動（熱鍵=\(DictateSettings.hotkeyLabel), AX=\(PermissionGuide.isAccessibilityTrusted)）")
    }

    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    private func openSettings() {
        settingsWindow.refreshStats()
        settingsWindow.show()
    }

    // MARK: - Push-to-talk

    private func pttPressed() {
        guard !isRecording else { return }
        if !jobs.isEmpty {
            mdLog("熱鍵按下：上一句還在轉錄（\(jobs.count) 句排隊），先錄下一句")
        }
        do {
            try recorder.start()
            isRecording = true
            refreshStatus()
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
        guard isRecording else { return }
        isRecording = false
        guard let result = recorder.stop() else {
            settleAfterRecording()
            return
        }

        guard result.wallSeconds >= minHoldSeconds, result.audioSeconds >= 0.2, !result.pcm16.isEmpty else {
            mdLog(String(format: "丟棄短錄音（wall %.2fs / audio %.2fs）", result.wallSeconds, result.audioSeconds))
            settleAfterRecording()
            return
        }

        // 錄到的是靜音（麥克風被靜音／選錯裝置）：送 daemon 也只會 no_speech，直接告訴使用者是哪支麥克風。
        if result.isSilent {
            mdLog(String(format: "麥克風收到靜音（%@，peak RMS %.1e）→ 不送 daemon", result.deviceName, result.peakRMS))
            let hint = MicDevice.isLidClosed()
                ? "筆電闔著，請接上或在選單「麥克風」選一支外接麥克風"
                : "到選單「麥克風」換一支，或檢查麥克風是否靜音"
            showError("麥克風收到靜音", body: "「\(result.deviceName)」沒有聲音。\(hint)", detail: result.deviceName)
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

        let job = Job(id: nextJobID, wavPath: wavPath, audioSeconds: result.audioSeconds, releasedAt: Date(),
                      targetPID: NSWorkspace.shared.frontmostApplication?.processIdentifier)
        nextJobID += 1
        jobs.append(job)
        refreshStatus()
        if DictateSettings.hudEnabled { hud.show(.transcribing(label: hudTranscribingLabel)) }
        transcribe(job)
    }

    /// 按住熱鍵時按了別的鍵（組合鍵）：這次不是要講話，錄到的丟掉、不送 daemon。
    private func pttCancelled() {
        guard isRecording else { return }
        isRecording = false
        _ = recorder.stop()
        settleAfterRecording()
    }

    /// 錄音結束但沒有送出新的一句：前面還有句子在轉就顯示轉錄中，沒有就收起 HUD
    private func settleAfterRecording() {
        refreshStatus()
        if jobs.isEmpty {
            hud.hide()
        } else if DictateSettings.hudEnabled {
            hud.show(.transcribing(label: hudTranscribingLabel))
        }
    }

    private static func makeWavPath() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let stamp = formatter.string(from: Date())
        let path = "/tmp/\(ProductConfig.productID)-rec-\(stamp).wav"
        if FileManager.default.fileExists(atPath: path) {
            let ms = Int(Date().timeIntervalSince1970 * 1000) % 1000
            return "/tmp/\(ProductConfig.productID)-rec-\(stamp)-\(String(format: "%03d", ms)).wav"
        }
        return path
    }

    // MARK: - Status

    /// 選單列圖示與控制中心：錄音中 > 轉錄中（含排隊句數）> 閒置
    private func refreshStatus() {
        errorResetWorkItem?.cancel()
        errorResetWorkItem = nil
        if isRecording {
            statusController.setState(.recording)
        } else if !jobs.isEmpty {
            statusController.setState(.transcribing)
        } else {
            statusController.setState(.idle)
        }
        statusController.setTranscribing(label: transcribingLabel, queued: jobs.count)
    }

    private var transcribingLabel: String {
        jobs.first?.slow == true ? "還在轉，轉完會補上…" : "轉錄中…"
    }

    private var hudTranscribingLabel: String {
        jobs.count > 1 ? "\(transcribingLabel)（\(jobs.count) 句）" : transcribingLabel
    }

    // MARK: - Daemon

    /// IO-CONTRACT 停損：固定 10s 對長口述 + llm_zh fallback 太短——2026-07-16 事故，117s
    /// 音檔 + llm_zh 閘門驗證失敗回退 smart_zh，total 16.875s 超過原門檻，殼誤判 daemon 逾時/離線，
    /// 但轉錄其實成功（daemon 端 error_count 0）。改隨音檔長度 + llm_zh 最壞情況 headroom 動態抓。
    /// v0.9：過了這個時間殼不再放棄，只是改顯示「還在轉…」繼續等（見 giveUpTimeout）。
    private static func transcribeTimeout(forAudioSeconds audioSeconds: Double) -> TimeInterval {
        let asrHeadroom = audioSeconds / 6  // 實測長音檔 ASR ≈ audio 的 1/13（117.4s→8.86s），抓 2x 安全係數
        // ⚠️ 這個數字跟 daemon 的 PUNCT_LLM_TIMEOUT_CAP_S 是一組契約，不可以只改一邊 ⚠️
        // daemon 的標點預算是長度感知的 min(4.0 + 字數×0.025, CAP)，CAP 目前 10.0，
        // 這裡的 12 是留給它 + 閘門驗證的 headroom。把 CAP 調大而不動這裡，
        // 長口述就會出現「殼判定 daemon 離線、daemon 其實在背景跑完」的假離線。
        // daemon 端照抄這條公式（shell_deadline_s）來排它自己的截止時間：改這裡要一起改那邊。
        let llmHeadroom: TimeInterval = 12
        return max(15, asrHeadroom + llmHeadroom)
    }

    /// 過了 transcribeTimeout 之後還在同一條連線上等多久（v0.9）。daemon 自己有截止時間，
    /// 等到這裡多半是它卡住或重啟了：改去讀它的最後結果檔（recover）。
    private static func giveUpTimeout(after soft: TimeInterval) -> TimeInterval {
        max(120, soft * 4)
    }

    private func transcribe(_ job: Job) {
        let soft = Self.transcribeTimeout(forAudioSeconds: job.audioSeconds)
        let client = SocketClient(timeout: Self.giveUpTimeout(after: soft))
        let punct = DictateSettings.punctMode
        let fillers = DictateSettings.stripFillers ? "strip" : nil
        let id = job.id, wavPath = job.wavPath
        socketQueue.async { [weak self] in
            let outcome: Result<DaemonResponse, Error> = Result {
                try client.roundTrip(.transcribe(wavPath: wavPath, punct: punct, fillers: fillers), slowAfter: soft) {
                    DispatchQueue.main.async { self?.markSlow(id, after: soft) }
                }
            }
            DispatchQueue.main.async {
                self?.handleTranscribeOutcome(outcome, jobID: id)
            }
        }
    }

    private func markSlow(_ id: Int, after soft: TimeInterval) {
        guard let i = jobs.firstIndex(where: { $0.id == id }) else { return }
        jobs[i].slow = true
        mdLog(String(format: "daemon %.0f 秒還沒回應 → 不放棄，繼續等（還在轉…）", soft))
        refreshStatus()
        if !isRecording, DictateSettings.hudEnabled { hud.show(.transcribing(label: hudTranscribingLabel)) }
    }

    private func handleTranscribeOutcome(_ outcome: Result<DaemonResponse, Error>, jobID: Int) {
        guard let i = jobs.firstIndex(where: { $0.id == jobID }) else { return }
        let job = jobs.remove(at: i)
        refreshStatus()
        switch outcome {
        case .success(let response):
            deliver(response, job: job, late: job.slow)
        case .failure(let error):
            let why = (error as? SocketClientError)?.description ?? error.localizedDescription
            recover(job, why: why)
        }
    }

    /// 一句的結果回來了。照順序、焦點沒換 → 打字；晚到又換了焦點、放開後接著打了字、或被後面那句超車
    /// → 不插進文件：放剪貼簿＋最近紀錄＋控制中心「剛剛沒送到的那句」。
    private func deliver(_ response: DaemonResponse, job: Job, late: Bool) {
        if response.ok, let text = response.text, !text.isEmpty {
            let latencyMs = response.totalMs.map { Int($0) }
            let changes = response.changes ?? []
            mdLog("轉錄 OK\(latencyMs.map { " (\($0)ms)" } ?? "")\(late ? "（晚到）" : "")：\(text.prefix(80))")
            statusController.setSuccess(text: text, raw: response.raw, latencyMs: latencyMs, changes: changes)
            statusController.pushHistory(text: text, raw: response.raw, changes: changes, latencyMs: latencyMs,
                                         uid: response.uid)
            settingsWindow.refreshStats()

            let holdBack = DeliveryPolicy.holdBack(
                jobID: job.id, lastDeliveredJobID: lastDeliveredJobID, late: late,
                targetPID: job.targetPID, frontPID: NSWorkspace.shared.frontmostApplication?.processIdentifier,
                releasedAt: job.releasedAt, lastUserInputAt: InsertionTracker.shared.lastUserInputAt)
            lastDeliveredJobID = max(lastDeliveredJobID, job.id)
            if let why = holdBack {
                mdLog("晚到的那句不插進文件（\(why)）→ 放剪貼簿")
                TextInjector.leaveOnClipboard(text)
                statusController.setUndelivered(text: text, raw: response.raw, changes: changes,
                                                latencyMs: latencyMs, uid: response.uid)
                Notifier.notify(title: "剛剛那句晚到了，已放剪貼簿", body: String(text.prefix(80)))
                if !isRecording, DictateSettings.hudEnabled {
                    hud.show(.notice(message: "晚到的那句已放剪貼簿", detail: text))
                }
                return
            }
            if !isRecording {
                if DictateSettings.hudEnabled {
                    hud.show(.success(text: text, raw: response.raw, latencyMs: latencyMs, changes: changes,
                                      label: late ? "晚到・已插入" : nil))
                } else {
                    hud.hide()
                }
            }
            TextInjector.inject(text) { method in mdLog("注入完成：\(method.rawValue)") }
        } else if response.error == DaemonResponse.errorNoSpeech || (response.ok && (response.text ?? "").isEmpty) {
            mdLog("no_speech，安靜返回")
            if !isRecording {
                if jobs.isEmpty {
                    hud.hide()
                } else if DictateSettings.hudEnabled {
                    hud.show(.transcribing(label: hudTranscribingLabel))
                }
            }
        } else {
            let code = response.error ?? "unknown"
            showError("轉錄失敗", body: "daemon 回報：\(code)")
        }
    }

    // MARK: - 逾時不丟（v0.9）

    /// socket 沒拿到結果（等到底、daemon 重啟、連不上）：不刪錄音、不放棄。
    /// 每 2 秒看一次 daemon 的最後結果檔；daemon 回來了但沒轉過這句（錄音檔還在）就重送一次；5 分鐘後才算真的丟了。
    private func recover(_ job: Job, why: String) {
        mdLog("daemon 沒有回應（\(why)）→ 不放棄：錄音留著，等結果檔或 daemon 回來")
        statusController.setDaemonStatus("沒回應，等它回來…")
        if !isRecording, DictateSettings.hudEnabled {
            hud.show(.notice(message: "辨識服務沒回應，等它回來…", detail: "這句的錄音還在，轉好會補上"))
        }
        pollRecovery(job, why: why, started: Date())
    }

    private func pollRecovery(_ job: Job, why: String, started: Date) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
            guard let self else { return }
            if let r = DaemonResults.find(wavPath: job.wavPath) {
                mdLog("從 daemon 結果檔撿回那句（等了 \(Int(Date().timeIntervalSince(started))) 秒）")
                self.deliver(r, job: job, late: true)
                self.pingDaemon()
                return
            }
            let waited = Date().timeIntervalSince(started)
            if !job.resent, waited >= 4, FileManager.default.fileExists(atPath: job.wavPath) {
                self.resendIfDaemonIsBack(job, why: why, started: started)
                return
            }
            guard waited < 300 else {
                mdLog("等了 5 分鐘還是沒有結果（\(why)）→ 放棄；錄音留在 \(job.wavPath)")
                self.showError("那句沒轉成功", body: "辨識服務一直沒回應（\(why)）。錄音留在 \(job.wavPath)")
                return
            }
            self.pollRecovery(job, why: why, started: started)
        }
    }

    /// daemon 回來了、結果檔沒有這句、錄音檔還在 ⇒ daemon 沒讀到它（重啟前排在後面、或當時連不上）→ 重送一次。
    /// daemon 還在忙（ping 等不到）就繼續等結果檔：它處理完會自己寫進去、也會自己刪錄音。
    private func resendIfDaemonIsBack(_ job: Job, why: String, started: Date) {
        let client = SocketClient(timeout: 2)
        socketQueue.async { [weak self] in
            let alive = (try? client.roundTrip(.ping))?.pong == true
            DispatchQueue.main.async {
                guard let self else { return }
                if let r = DaemonResults.find(wavPath: job.wavPath) {
                    self.deliver(r, job: job, late: true)
                    return
                }
                guard alive, FileManager.default.fileExists(atPath: job.wavPath) else {
                    self.pollRecovery(job, why: why, started: started)
                    return
                }
                mdLog("daemon 回來了、那句沒被轉過 → 重送一次")
                var again = job
                again.resent = true
                again.slow = true                     // 算晚到：焦點換了就放剪貼簿
                self.jobs.append(again)
                self.refreshStatus()
                self.transcribe(again)
            }
        }
    }

    // MARK: - 重轉／撤回上一句（v0.9）

    /// 上一句用準模式重轉：daemon 拿留著的那段錄音跑 Whisper，回來後換掉打進去的字。
    private func retranscribe(_ u: ControlCenterModel.Utterance) {
        guard let uid = u.uid else { return }
        mdLog("用準模式重轉上一句（\(uid)）")
        statusController.setRedoBusy(true)
        if DictateSettings.hudEnabled, !isRecording { hud.show(.transcribing(label: "用準模式重轉…")) }
        let client = SocketClient(timeout: 60)
        let punct = DictateSettings.punctMode
        let fillers = DictateSettings.stripFillers ? "strip" : nil
        socketQueue.async { [weak self] in
            let outcome = Result { try client.roundTrip(.retranscribe(uid: uid, punct: punct, fillers: fillers)) }
            DispatchQueue.main.async { self?.handleRetranscribe(outcome, original: u) }
        }
    }

    private func handleRetranscribe(_ outcome: Result<DaemonResponse, Error>, original u: ControlCenterModel.Utterance) {
        statusController.setRedoBusy(false)
        let r: DaemonResponse
        switch outcome {
        case .failure(let e):
            showError("重轉失敗", body: (e as? SocketClientError)?.description ?? e.localizedDescription)
            return
        case .success(let resp):
            r = resp
        }
        guard r.ok, let text = r.text, !text.isEmpty else {
            switch r.error {
            case "file_not_found":
                let keep = recentAudioSeconds.map { "只留 \(Int($0)) 秒" } ?? "已過期"
                notice("錄音已經刪了（\(keep)），沒辦法重轉")
            case DaemonResponse.errorNoSpeech?, nil:
                notice("準模式沒聽到話", detail: "原本那句沒動")
            default:
                showError("重轉失敗", body: "daemon 回報：\(r.error ?? "unknown")")
            }
            return
        }
        let latencyMs = r.totalMs.map { Int($0) }
        mdLog("準模式重轉 OK\(latencyMs.map { " (\($0)ms)" } ?? "")：\(text.prefix(80))")
        guard text != u.text else {
            notice("準模式聽到的一樣", detail: text)
            return
        }
        statusController.setSuccess(text: text, raw: r.raw, latencyMs: latencyMs, changes: r.changes ?? [])
        statusController.pushHistory(text: text, raw: r.raw, changes: r.changes ?? [], latencyMs: latencyMs,
                                     uid: r.uid, redoOf: r.redoOf ?? u.uid)
        TextInjector.replaceLast(u.text, with: text) { [weak self] result in
            guard let self else { return }
            switch result {
            case .done(let how):
                mdLog("已換成準模式的結果（\(how)）")
                if DictateSettings.hudEnabled, !self.isRecording {
                    self.hud.show(.success(text: text, raw: nil, latencyMs: latencyMs, changes: r.changes,
                                           label: "已換成準模式"))
                }
            case .unsafe(let why):
                mdLog("沒有自動換字（\(why)）→ 準模式的結果放剪貼簿")
                TextInjector.leaveOnClipboard(text)
                self.notice("準模式的結果在剪貼簿", detail: why)
            }
        }
    }

    /// 撤回上一句：把剛打進去的那句刪掉（焦點已經還給原本的 app）。
    private func withdraw(_ u: ControlCenterModel.Utterance) {
        TextInjector.replaceLast(u.text, with: "") { [weak self] result in
            guard let self else { return }
            switch result {
            case .done(let how):
                mdLog("撤回上一句（\(how)，\(u.text.count) 字）")
                self.statusController.markWithdrawn(u)
                if DictateSettings.hudEnabled, !self.isRecording {
                    self.hud.show(.success(text: u.text, raw: nil, latencyMs: nil, changes: nil, label: "已撤回"))
                }
            case .unsafe(let why):
                mdLog("沒有撤回（\(why)）")
                self.notice("沒有自動撤回", detail: why)
            }
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
                    let model = (r.asrMain ?? r.model)?.split(separator: "/").last.map(String.init) ?? "?"
                    self?.lastDaemonModel = model
                    let ver = r.version.map { " v\($0)" } ?? ""
                    let modeLabel = r.mode == "fast" ? "快・" : (r.mode == "accurate" ? "準・" : "")
                    let status = "線上 ✓（\(modeLabel)\(model), \(warm)\(ver)）"
                    self?.statusController.setAsrMode(r.mode)
                    self?.statusController.setDaemonStatus(status)
                    self?.settingsWindow.setDaemonStatus(status, model: r.model)
                    self?.recentAudioSeconds = r.recentAudioSeconds
                    self?.statusController.setRedoSupport(seconds: r.recentAudioSeconds)
                    if let path = r.resultsPath { DaemonResults.path = path }
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

    /// 切辨識模式（daemon v0.7+）：daemon 當場換模型，下一句生效，不用重啟。
    private func setAsrMode(_ mode: String) {
        let client = SocketClient(timeout: 5)
        socketQueue.async { [weak self] in
            let outcome = Result { try client.roundTrip(.setMode(mode)) }
            DispatchQueue.main.async {
                switch outcome {
                case .success(let r) where r.ok:
                    self?.statusController.setAsrMode(r.mode)
                    let label = r.mode == "fast" ? "快" : "準"
                    Notifier.notify(title: "辨識模式：\(label)", body: r.asrMain ?? "")
                    self?.pingDaemon()
                case .success(let r):
                    Notifier.notify(title: "切換辨識模式失敗", body: r.error ?? "daemon 不支援（要 v0.7＋router 模式）")
                case .failure(let e):
                    let why = (e as? SocketClientError)?.description ?? e.localizedDescription
                    Notifier.notify(title: "切換辨識模式失敗", body: why)
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
        let museBotRoot = ProductConfig.lexiconRoot
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
        p.arguments = ["kickstart", "-k", "gui/\(getuid())/\(ProductConfig.daemonLaunchLabel)"]
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

    private func showError(_ title: String, body: String, detail: String? = nil) {
        statusController.setLastResult("⚠️ \(title)：\(body)")
        Notifier.notify(title: title, body: body)
        guard !isRecording else { return }          // 正在錄下一句：不蓋掉錄音的提示
        statusController.setState(.error)
        if DictateSettings.hudEnabled {
            hud.show(.error(message: title, detail: detail))
        } else {
            hud.hide()
        }

        errorResetWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.refreshStatus() }
        errorResetWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: work)
    }

    /// 不是錯、但要讓人知道（字放剪貼簿、沒有自動撤回…）：HUD 黃色提示＋控制中心一條橫幅
    private func notice(_ message: String, detail: String? = nil) {
        statusController.setLastResult("⚠️ \(message)\(detail.map { "：\($0)" } ?? "")")
        if DictateSettings.hudEnabled, !isRecording { hud.show(.notice(message: message, detail: detail)) }
    }
}
