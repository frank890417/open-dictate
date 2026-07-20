import AppKit

/// menubar 狀態圖示 + 選單（SF Symbols + 分區）。
final class StatusItemController: NSObject, NSMenuDelegate {

    enum IconState {
        case idle, recording, transcribing, error

        var symbol: String {
            switch self {
            case .idle: return UITheme.Symbol.idle
            case .recording: return UITheme.Symbol.recording
            case .transcribing: return UITheme.Symbol.transcribing
            case .error: return UITheme.Symbol.error
            }
        }

        var tint: NSColor? {
            switch self {
            case .idle: return nil
            case .recording: return UITheme.record
            case .transcribing: return UITheme.transcribe
            case .error: return UITheme.error
            }
        }

        var tooltip: String {
            let hk = DictateSettings.hotkeyLabel
            switch self {
            case .idle: return "OpenDictate — 按住 \(hk) 講話"
            case .recording: return "錄音中…"
            case .transcribing: return "轉錄中…"
            case .error: return "發生錯誤"
            }
        }
    }

    var onReloadLexicon: (() -> Void)?
    var onCheckDaemon: (() -> Void)?
    var onRestartDaemon: (() -> Void)?
    var onReportMishear: ((String, String) -> Void)?
    var onOpenSettings: (() -> Void)?
    var onOpenLog: (() -> Void)?
    var onHotkeyChanged: (() -> Void)?
    var onMicChanged: (() -> Void)?

    private var statusItem: NSStatusItem!
    private let daemonStatusMenuItem = NSMenuItem(title: "daemon：尚未檢查", action: nil, keyEquivalent: "")
    private let statsMenuItem = NSMenuItem(title: "今日尚無聽寫", action: nil, keyEquivalent: "")
    private let lastResultMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let latencyMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let copyLastMenuItem = NSMenuItem(title: "複製上一句", action: #selector(copyLastClicked), keyEquivalent: "c")
    private let copyRawMenuItem = NSMenuItem(title: "複製上一句 raw", action: #selector(copyRawClicked), keyEquivalent: "")
    private let teachChangesMenuItem = NSMenuItem(title: "從上一句教詞庫", action: nil, keyEquivalent: "")
    private let teachChangesSubmenu = NSMenu()

    private let punctMenuItem = NSMenuItem(title: "標點模式", action: nil, keyEquivalent: "")
    private let punctSubmenu = NSMenu()
    private let punctSmartItem = NSMenuItem(title: "智慧全形（規則層，快）", action: #selector(punctModeClicked(_:)), keyEquivalent: "")
    private let punctLLMItem = NSMenuItem(title: "LLM 完整標點（+1 秒）", action: #selector(punctModeClicked(_:)), keyEquivalent: "")
    private let punctRawItem = NSMenuItem(title: "原樣（whisper 直出）", action: #selector(punctModeClicked(_:)), keyEquivalent: "")

    private let hotkeyMenuItem = NSMenuItem(title: "熱鍵", action: nil, keyEquivalent: "")
    private let hotkeySubmenu = NSMenu()
    private let hotkeyFnItem = NSMenuItem(title: "fn", action: #selector(hotkeyClicked(_:)), keyEquivalent: "")
    private let hotkeyOptItem = NSMenuItem(title: "右 Option", action: #selector(hotkeyClicked(_:)), keyEquivalent: "")

    private let micMenuItem = NSMenuItem(title: "麥克風", action: nil, keyEquivalent: "")
    private let micSubmenu = NSMenu()

    private let injectMenuItem = NSMenuItem(title: "注入方式", action: nil, keyEquivalent: "")
    private let injectSubmenu = NSMenu()
    private let injectAutoItem = NSMenuItem(title: "自動（AX → 貼上）", action: #selector(injectClicked(_:)), keyEquivalent: "")
    private let injectAXItem = NSMenuItem(title: "僅 AX 直寫", action: #selector(injectClicked(_:)), keyEquivalent: "")
    private let injectPasteItem = NSMenuItem(title: "剪貼簿 Cmd-V", action: #selector(injectClicked(_:)), keyEquivalent: "")

    private let hudMenuItem = NSMenuItem(title: "錄音浮動提示（游標旁）", action: #selector(hudToggled), keyEquivalent: "")
    private let rawDiffMenuItem = NSMenuItem(title: "成功時顯示 raw 對照", action: #selector(rawDiffToggled), keyEquivalent: "")

    private var lastText: String?
    private var lastRaw: String?
    private var lastChanges: [[String]] = []
    private var lastAppliedLogTs: String?
    private var history: [(text: String, raw: String?)] = []
    private let historyMenuItem = NSMenuItem(title: "最近紀錄", action: nil, keyEquivalent: "")
    private let historySubmenu = NSMenu()

    func setup() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        applyIcon(.idle)

        let menu = NSMenu()
        menu.delegate = self

        daemonStatusMenuItem.isEnabled = false
        menu.addItem(daemonStatusMenuItem)

        statsMenuItem.isEnabled = false
        menu.addItem(statsMenuItem)

        lastResultMenuItem.isEnabled = false
        lastResultMenuItem.isHidden = true
        menu.addItem(lastResultMenuItem)

        latencyMenuItem.isEnabled = false
        latencyMenuItem.isHidden = true
        menu.addItem(latencyMenuItem)

        copyLastMenuItem.target = self
        copyLastMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.copy)
        copyLastMenuItem.isHidden = true
        menu.addItem(copyLastMenuItem)

        copyRawMenuItem.target = self
        copyRawMenuItem.isHidden = true
        menu.addItem(copyRawMenuItem)

        teachChangesMenuItem.submenu = teachChangesSubmenu
        teachChangesMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.mishear)
        teachChangesMenuItem.isHidden = true
        menu.addItem(teachChangesMenuItem)

        historyMenuItem.submenu = historySubmenu
        historyMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.history)
        historyMenuItem.isHidden = true
        menu.addItem(historyMenuItem)

        let mishear = NSMenuItem(title: "回報誤聽（教詞庫）…", action: #selector(reportMishearClicked), keyEquivalent: "t")
        mishear.target = self
        mishear.image = UITheme.menuSymbol(UITheme.Symbol.mishear)
        menu.addItem(mishear)

        let teachSel = NSMenuItem(title: "教選取文字…", action: #selector(teachSelectionClicked), keyEquivalent: "e")
        teachSel.target = self
        teachSel.image = UITheme.menuSymbol(UITheme.Symbol.mishear)
        teachSel.toolTip = "先在文件中選取聽錯的詞，再點此填入「聽錯」欄"
        menu.addItem(teachSel)

        menu.addItem(.separator())

        // 標點
        punctSmartItem.target = self; punctSmartItem.representedObject = "smart_zh"
        punctLLMItem.target = self; punctLLMItem.representedObject = "llm_zh"
        punctRawItem.target = self; punctRawItem.representedObject = "raw"
        punctSubmenu.addItem(punctSmartItem)
        punctSubmenu.addItem(punctLLMItem)
        punctSubmenu.addItem(punctRawItem)
        punctMenuItem.submenu = punctSubmenu
        menu.addItem(punctMenuItem)

        // 熱鍵
        hotkeyFnItem.target = self; hotkeyFnItem.representedObject = "fn"
        hotkeyOptItem.target = self; hotkeyOptItem.representedObject = "rightOption"
        hotkeySubmenu.addItem(hotkeyFnItem)
        hotkeySubmenu.addItem(hotkeyOptItem)
        hotkeyMenuItem.submenu = hotkeySubmenu
        menu.addItem(hotkeyMenuItem)

        // 麥克風
        micMenuItem.submenu = micSubmenu
        menu.addItem(micMenuItem)

        // 注入
        injectAutoItem.target = self; injectAutoItem.representedObject = "auto"
        injectAXItem.target = self; injectAXItem.representedObject = "ax"
        injectPasteItem.target = self; injectPasteItem.representedObject = "paste"
        injectSubmenu.addItem(injectAutoItem)
        injectSubmenu.addItem(injectAXItem)
        injectSubmenu.addItem(injectPasteItem)
        injectMenuItem.submenu = injectSubmenu
        menu.addItem(injectMenuItem)

        hudMenuItem.target = self
        menu.addItem(hudMenuItem)
        rawDiffMenuItem.target = self
        menu.addItem(rawDiffMenuItem)

        let settings = NSMenuItem(title: "設定…", action: #selector(settingsClicked), keyEquivalent: ",")
        settings.target = self
        settings.image = UITheme.menuSymbol(UITheme.Symbol.settings)
        menu.addItem(settings)

        menu.addItem(.separator())

        let reload = NSMenuItem(title: "重載詞庫", action: #selector(reloadLexiconClicked), keyEquivalent: "r")
        reload.target = self
        reload.image = UITheme.menuSymbol(UITheme.Symbol.lexicon)
        menu.addItem(reload)

        let restart = NSMenuItem(title: "重啟 daemon", action: #selector(restartDaemonClicked), keyEquivalent: "")
        restart.target = self
        restart.image = UITheme.menuSymbol(UITheme.Symbol.restart)
        menu.addItem(restart)

        let openLog = NSMenuItem(title: "開啟今日聽寫記錄", action: #selector(openLogClicked), keyEquivalent: "")
        openLog.target = self
        openLog.image = UITheme.menuSymbol(UITheme.Symbol.log)
        menu.addItem(openLog)

        let permissions = NSMenuItem(title: "權限說明…", action: #selector(permissionsClicked), keyEquivalent: "")
        permissions.target = self
        permissions.image = UITheme.menuSymbol(UITheme.Symbol.permissions)
        menu.addItem(permissions)

        menu.addItem(.separator())

        let quit = NSMenuItem(title: "結束 OpenDictate", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.image = UITheme.menuSymbol(UITheme.Symbol.quit)
        menu.addItem(quit)

        statusItem.menu = menu
    }

    // MARK: - External updates

    func setState(_ state: IconState) { applyIcon(state) }

    private func applyIcon(_ state: IconState) {
        guard let button = statusItem.button else { return }
        button.image = UITheme.symbolImage(state.symbol)
        button.title = ""
        button.contentTintColor = state.tint
        button.toolTip = state.tooltip
    }

    func setDaemonStatus(_ text: String) {
        daemonStatusMenuItem.title = "daemon：\(text)"
        if text.contains("離線") || text.contains("連不上") {
            daemonStatusMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.offline)
        } else if text.contains("線上") {
            daemonStatusMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.success)
        } else {
            daemonStatusMenuItem.image = nil
        }
    }

    func refreshStats() {
        statsMenuItem.title = DictationStats.loadToday().summaryLine
    }

    /// 殼端 timeout 但 daemon 其實跑完時的復原：lastText 只在收到 socket 回應時更新，
    /// 開選單時額外核對 log 檔最後一筆成功記錄，避免「複製上一句」拿不到剛才其實成功的那句
    /// （2026-07-16 事故：daemon 完成但殼已判定逾時，in-memory lastText 停在更早那句）。
    private func recoverLastFromLogIfNeeded() {
        guard let entry = DictationStats.lastSuccessEntry(), entry.ts != lastAppliedLogTs else { return }
        lastAppliedLogTs = entry.ts
        guard entry.text != lastText else { return }
        setSuccess(text: entry.text, raw: entry.raw, latencyMs: entry.totalMs, changes: entry.changes)
        pushHistory(text: entry.text, raw: entry.raw)
    }

    func setLastResult(_ text: String?) {
        if let text, !text.isEmpty {
            lastResultMenuItem.title = String(text.prefix(60))
            lastResultMenuItem.isHidden = false
            if text.hasPrefix("上一句：") {
                lastText = String(text.dropFirst("上一句：".count))
                copyLastMenuItem.isHidden = false
            }
        } else {
            lastResultMenuItem.isHidden = true
            copyLastMenuItem.isHidden = true
        }
    }

    func setSuccess(text: String, raw: String?, latencyMs: Int?, changes: [[String]]) {
        lastText = text
        lastRaw = raw
        lastChanges = changes
        lastResultMenuItem.title = "上一句：" + String(text.prefix(48)) + (text.count > 48 ? "…" : "")
        lastResultMenuItem.isHidden = false
        copyLastMenuItem.isHidden = false
        copyRawMenuItem.isHidden = (raw == nil || raw == text || (raw ?? "").isEmpty)

        if let ms = latencyMs {
            let changeNote = changes.isEmpty ? "" : " · 詞庫 \(changes.count) 處"
            latencyMenuItem.title = "  ↳ \(ms)ms\(changeNote)"
            latencyMenuItem.isHidden = false
        } else {
            latencyMenuItem.isHidden = true
        }

        rebuildTeachMenu()
        refreshStats()
    }

    func pushHistory(text: String, raw: String?) {
        history.insert((text, raw), at: 0)
        if history.count > 8 { history.removeLast(history.count - 8) }
        historySubmenu.removeAllItems()
        for (i, h) in history.enumerated() {
            let item = NSMenuItem(
                title: "\(i + 1). \(String(h.text.prefix(48)))\(h.text.count > 48 ? "…" : "")",
                action: #selector(historyItemClicked(_:)),
                keyEquivalent: ""
            )
            item.target = self
            item.representedObject = h.text
            historySubmenu.addItem(item)
        }
        historySubmenu.addItem(.separator())
        let hint = NSMenuItem(title: "點任一句＝複製全文", action: nil, keyEquivalent: "")
        hint.isEnabled = false
        historySubmenu.addItem(hint)
        historyMenuItem.isHidden = false
    }

    private func rebuildTeachMenu() {
        teachChangesSubmenu.removeAllItems()
        var hasAny = false

        if !lastChanges.isEmpty {
            hasAny = true
            for pair in lastChanges {
                guard pair.count >= 2 else { continue }
                let wrong = pair[0], right = pair[1]
                let item = NSMenuItem(title: "✓ \(wrong) → \(right)", action: nil, keyEquivalent: "")
                item.isEnabled = false
                teachChangesSubmenu.addItem(item)
            }
            teachChangesSubmenu.addItem(.separator())
        }

        let suggestions = TeachSuggestions.pairs(raw: lastRaw, text: lastText)
        if !suggestions.isEmpty {
            hasAny = true
            let lab = NSMenuItem(title: "建議教這些：", action: nil, keyEquivalent: "")
            lab.isEnabled = false
            teachChangesSubmenu.addItem(lab)
            for (i, pair) in suggestions.enumerated() {
                let item = NSMenuItem(
                    title: "\(pair.wrong) → \(pair.right)",
                    action: #selector(suggestionClicked(_:)),
                    keyEquivalent: ""
                )
                item.target = self
                item.representedObject = [pair.wrong, pair.right]
                item.tag = i
                teachChangesSubmenu.addItem(item)
            }
            teachChangesSubmenu.addItem(.separator())
        }

        if let raw = lastRaw, let text = lastText, raw != text {
            hasAny = true
            let both = NSMenuItem(title: "對照 raw / 校正後…", action: #selector(reportMishearWithContext), keyEquivalent: "")
            both.target = self
            teachChangesSubmenu.addItem(both)
        }

        let manual = NSMenuItem(title: "手動填寫…", action: #selector(reportMishearClicked), keyEquivalent: "")
        manual.target = self
        teachChangesSubmenu.addItem(manual)

        let sel = NSMenuItem(title: "從選取文字…", action: #selector(teachSelectionClicked), keyEquivalent: "")
        sel.target = self
        teachChangesSubmenu.addItem(sel)

        teachChangesMenuItem.isHidden = lastText == nil && !hasAny
        if lastText != nil { teachChangesMenuItem.isHidden = false }
    }

    // MARK: - Menu open

    func menuWillOpen(_ menu: NSMenu) {
        daemonStatusMenuItem.title = "daemon：檢查中…"
        daemonStatusMenuItem.image = nil
        refreshStats()
        recoverLastFromLogIfNeeded()

        let mode = DictateSettings.punctMode
        punctSmartItem.state = mode == "smart_zh" ? .on : .off
        punctLLMItem.state = mode == "llm_zh" ? .on : .off
        punctRawItem.state = mode == "raw" ? .on : .off

        let hk = DictateSettings.hotkey
        hotkeyFnItem.state = hk == "fn" ? .on : .off
        hotkeyOptItem.state = hk == "rightOption" ? .on : .off
        hotkeyMenuItem.title = "熱鍵（\(DictateSettings.hotkeyLabel)）"

        let inj = DictateSettings.injectMode
        injectAutoItem.state = inj == "auto" ? .on : .off
        injectAXItem.state = inj == "ax" ? .on : .off
        injectPasteItem.state = inj == "paste" ? .on : .off

        hudMenuItem.state = DictateSettings.hudEnabled ? .on : .off
        rawDiffMenuItem.state = DictateSettings.showRawDiff ? .on : .off

        rebuildMicMenu()
        onCheckDaemon?()
    }

    private func rebuildMicMenu() {
        micSubmenu.removeAllItems()
        let preferred = DictateSettings.preferredMicUID

        let sys = NSMenuItem(title: "系統預設", action: #selector(micClicked(_:)), keyEquivalent: "")
        sys.target = self
        sys.representedObject = ""
        sys.state = preferred == nil ? .on : .off
        micSubmenu.addItem(sys)
        micSubmenu.addItem(.separator())

        for dev in MicDevice.listInputs() {
            let title = dev.isDefault ? "\(dev.name)（預設）" : dev.name
            let item = NSMenuItem(title: title, action: #selector(micClicked(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = dev.uid
            item.state = preferred == dev.uid ? .on : .off
            micSubmenu.addItem(item)
        }
        micMenuItem.title = "麥克風（\(MicDevice.preferredDisplayName())）"
    }

    // MARK: - Actions

    @objc private func reloadLexiconClicked() { onReloadLexicon?() }
    @objc private func restartDaemonClicked() { onRestartDaemon?() }
    @objc private func settingsClicked() { onOpenSettings?() }

    @objc private func punctModeClicked(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String else { return }
        DictateSettings.punctMode = mode
    }

    @objc private func hotkeyClicked(_ sender: NSMenuItem) {
        guard let v = sender.representedObject as? String else { return }
        DictateSettings.hotkey = v
        onHotkeyChanged?()
    }

    @objc private func injectClicked(_ sender: NSMenuItem) {
        guard let v = sender.representedObject as? String else { return }
        DictateSettings.injectMode = v
    }

    @objc private func micClicked(_ sender: NSMenuItem) {
        let uid = sender.representedObject as? String ?? ""
        DictateSettings.preferredMicUID = uid.isEmpty ? nil : uid
        onMicChanged?()
    }

    @objc private func hudToggled() {
        DictateSettings.hudEnabled.toggle()
        hudMenuItem.state = DictateSettings.hudEnabled ? .on : .off
    }

    @objc private func rawDiffToggled() {
        DictateSettings.showRawDiff.toggle()
        rawDiffMenuItem.state = DictateSettings.showRawDiff ? .on : .off
    }

    @objc private func copyLastClicked() {
        guard let lastText, !lastText.isEmpty else { return }
        copyToPasteboard(lastText)
    }

    @objc private func copyRawClicked() {
        guard let lastRaw, !lastRaw.isEmpty else { return }
        copyToPasteboard(lastRaw)
    }

    @objc private func historyItemClicked(_ sender: NSMenuItem) {
        guard let full = sender.representedObject as? String else { return }
        copyToPasteboard(full)
    }

    private func copyToPasteboard(_ s: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(s, forType: .string)
    }

    @objc private func reportMishearClicked() {
        let suggestions = TeachSuggestions.pairs(raw: lastRaw, text: lastText)
        MishearPanel.present(
            seedWrong: nil,
            seedRight: nil,
            contextHint: lastText.map { "上一句：\($0)" } ?? lastRaw,
            suggestions: suggestions
        ) { [weak self] w, r in
            self?.onReportMishear?(w, r)
        }
    }

    @objc private func reportMishearWithContext() {
        let suggestions = TeachSuggestions.pairs(raw: lastRaw, text: lastText)
        MishearPanel.present(
            seedWrong: suggestions.first?.wrong,
            seedRight: suggestions.first?.right,
            contextHint: "raw：\(lastRaw ?? "")\n校正：\(lastText ?? "")",
            suggestions: suggestions
        ) { [weak self] w, r in
            self?.onReportMishear?(w, r)
        }
    }

    @objc private func teachSelectionClicked() {
        MishearPanel.presentFromSelection { [weak self] w, r in
            self?.onReportMishear?(w, r)
        }
    }

    @objc private func suggestionClicked(_ sender: NSMenuItem) {
        guard let pair = sender.representedObject as? [String], pair.count >= 2 else { return }
        MishearPanel.present(
            seedWrong: pair[0],
            seedRight: pair[1],
            contextHint: lastText.map { "上一句：\($0)" },
            suggestions: TeachSuggestions.pairs(raw: lastRaw, text: lastText)
        ) { [weak self] w, r in
            self?.onReportMishear?(w, r)
        }
    }

    @objc private func openLogClicked() {
        if let onOpenLog { onOpenLog(); return }
        openTodayLog()
    }

    func openTodayLog() {
        let today = DictationStats.todayFile()
        let dir = DictationStats.logDir
        if FileManager.default.fileExists(atPath: today.path) {
            NSWorkspace.shared.activateFileViewerSelecting([today])
        } else {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            NSWorkspace.shared.activateFileViewerSelecting([dir])
        }
    }

    @objc private func permissionsClicked() {
        PermissionGuide.showGuideAlert()
    }
}
