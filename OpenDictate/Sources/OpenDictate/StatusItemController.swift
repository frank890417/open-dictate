import AppKit
import SwiftUI

/// menubar 狀態圖示：左鍵＝控制中心（ControlCenter.swift），右鍵／Control-點＝完整選單（SF Symbols + 分區）。
final class StatusItemController: NSObject, NSMenuDelegate, NSPopoverDelegate {

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
            case .idle: return "\(ProductConfig.appName) — 按住 \(hk) 講話"
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
    var onSetAsrMode: ((String) -> Void)?
    var onOpenSettings: (() -> Void)?
    var onOpenLog: (() -> Void)?
    var onHotkeyChanged: (() -> Void)?
    var onMicChanged: (() -> Void)?

    private var statusItem: NSStatusItem!
    private let classicMenu = NSMenu()
    private let popover = NSPopover()
    private let model = ControlCenterModel()
    /// 開控制中心前在前景的 app：關掉時把焦點還給它（再貼一次也貼到它）
    private var previousApp: NSRunningApplication?
    /// 從控制中心開了需要打字的視窗（教詞庫、設定）→ 關控制中心時不要把焦點搶回原 app
    private var keepFocusOnClose = false
    private let daemonStatusMenuItem = NSMenuItem(title: "daemon：尚未檢查", action: nil, keyEquivalent: "")
    private let statsMenuItem = NSMenuItem(title: "今日尚無聽寫", action: nil, keyEquivalent: "")
    private let lastResultMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let latencyMenuItem = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let copyLastMenuItem = NSMenuItem(title: "複製上一句", action: #selector(copyLastClicked), keyEquivalent: "c")
    private let copyRawMenuItem = NSMenuItem(title: "複製上一句 raw", action: #selector(copyRawClicked), keyEquivalent: "")
    private let teachChangesMenuItem = NSMenuItem(title: "從上一句教詞庫", action: nil, keyEquivalent: "")
    private let teachChangesSubmenu = NSMenu()

    // 辨識模式（daemon v0.7+ router）：快＝Qwen3-ASR（自帶標點）／準＝Whisper＋提示詞；模型怎麼選見 docs/RECOGNITION-MODES.md
    private let asrModeMenuItem = NSMenuItem(title: "辨識模式", action: nil, keyEquivalent: "")
    private let asrModeSubmenu = NSMenu()
    private let asrFastItem = NSMenuItem(title: "快（Qwen3-ASR・自帶標點）", action: #selector(asrModeClicked(_:)), keyEquivalent: "")
    private let asrAccurateItem = NSMenuItem(title: "準（Whisper＋提示詞）", action: #selector(asrModeClicked(_:)), keyEquivalent: "")
    private var asrMode: String?
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
        if let button = statusItem.button {
            button.target = self
            button.action = #selector(statusButtonClicked(_:))
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }
        setupControlCenter()

        let menu = classicMenu
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

        // 辨識模式（舊 daemon 沒有模式 → 隱藏，第一次 ping 回來才顯示）
        asrFastItem.target = self; asrFastItem.representedObject = "fast"
        asrAccurateItem.target = self; asrAccurateItem.representedObject = "accurate"
        asrModeSubmenu.addItem(asrFastItem)
        asrModeSubmenu.addItem(asrAccurateItem)
        asrModeMenuItem.submenu = asrModeSubmenu
        asrModeMenuItem.isHidden = true
        menu.addItem(asrModeMenuItem)

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

        let quit = NSMenuItem(title: "結束 \(ProductConfig.appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        quit.image = UITheme.menuSymbol(UITheme.Symbol.quit)
        menu.addItem(quit)

        // 不掛 statusItem.menu：左鍵要開控制中心，完整選單在右鍵時才臨時掛上（showClassicMenu）
    }

    // MARK: - Control center

    /// 外部叫控制中心（`--probe-toggle`、Raycast／捷徑）：同一個 bundle id 底下的分散式通知
    static let toggleNotification = Notification.Name("\(ProductConfig.productID).toggleControlCenter")

    private func setupControlCenter() {
        DistributedNotificationCenter.default().addObserver(
            forName: Self.toggleNotification, object: nil, queue: .main) { [weak self] _ in self?.togglePopover() }
        let hosting = NSHostingController(rootView: ControlCenterView(model: model))
        hosting.sizingOptions = [.preferredContentSize]
        popover.contentViewController = hosting
        popover.behavior = .transient
        popover.animates = true
        popover.delegate = self

        model.onSetMode = { [weak self] mode in
            guard let self, mode != self.asrMode else { return }
            self.onSetAsrMode?(mode)
        }
        model.onPopMenu = { [weak self] kind in self?.popMenu(kind) }
        model.onHotkeyChanged = { [weak self] in self?.onHotkeyChanged?() }
        model.onRepaste = { [weak self] text in self?.repaste(text) }
        model.onReloadLexicon = { [weak self] in self?.onReloadLexicon?() }
        model.onRestartDaemon = { [weak self] in self?.onRestartDaemon?() }
        model.onOpenLog = { [weak self] in self?.closePopover(); self?.openLogClicked() }
        model.onOpenSettings = { [weak self] in
            self?.keepFocusOnClose = true
            self?.closePopover()
            self?.onOpenSettings?()
        }
        model.onPermissions = { [weak self] in
            self?.keepFocusOnClose = true
            self?.closePopover()
            PermissionGuide.showGuideAlert()
        }
        model.onStartMeeting = { [weak self] in
            self?.closePopover()
            if let url = URL(string: ProductConfig.meetingURL) { NSWorkspace.shared.open(url) }
        }

        // 重開 app 後控制中心不是空的：今天最後幾句從 log 讀回來
        for entry in DictationStats.recentSuccessEntries(limit: 6).reversed() {
            pushHistory(text: entry.text, raw: entry.raw, changes: entry.changes,
                        latencyMs: entry.totalMs, at: entry.date)
        }
        if let first = DictationStats.recentSuccessEntries(limit: 1).first {
            lastAppliedLogTs = first.ts
            lastText = first.text; lastRaw = first.raw; lastChanges = first.changes
            rebuildTeachMenu()
        }
    }

    @objc private func statusButtonClicked(_ sender: NSStatusBarButton) {
        let event = NSApp.currentEvent
        if event?.type == .rightMouseUp || event?.modifierFlags.contains(.control) == true {
            showClassicMenu()
        } else {
            togglePopover()
        }
    }

    private func showClassicMenu() {
        closePopover()
        statusItem.menu = classicMenu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil
    }

    private func togglePopover() {
        if popover.isShown { closePopover(); return }
        guard let button = statusItem.button else { return }
        let front = NSWorkspace.shared.frontmostApplication
        previousApp = front?.processIdentifier == ProcessInfo.processInfo.processIdentifier ? previousApp : front
        keepFocusOnClose = false
        recoverLastFromLogIfNeeded()
        model.refresh()
        onCheckDaemon?()
        NSApp.activate()
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
    }

    private func closePopover() {
        if popover.isShown { popover.performClose(nil) }
    }

    func popoverDidClose(_ notification: Notification) {
        // 用 Esc／按鈕關掉（我們還在前景）才把焦點還回去；點了別的 app 關掉的就不搶
        if !keepFocusOnClose, NSApp.isActive { previousApp?.activate() }
        keepFocusOnClose = false
    }

    /// 再貼一次：關控制中心 → 焦點還給原本的 app → 貼上
    private func repaste(_ text: String) {
        keepFocusOnClose = true
        closePopover()
        previousApp?.activate()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            TextInjector.inject(text) { method in mdLog("再貼一次：\(method.rawValue)") }
        }
    }

    private func popMenu(_ kind: ControlCenterModel.MenuKind) {
        let menu: NSMenu
        switch kind {
        case .mic:
            rebuildMicMenu()
            menu = micSubmenu
        case .punct:
            let mode = DictateSettings.punctMode
            punctSmartItem.state = mode == "smart_zh" ? .on : .off
            punctLLMItem.state = mode == "llm_zh" ? .on : .off
            punctRawItem.state = mode == "raw" ? .on : .off
            menu = punctSubmenu
        case .teach:
            rebuildTeachMenu()
            keepFocusOnClose = true        // 教詞庫面板要打字
            menu = teachChangesSubmenu
        }
        menu.popUp(positioning: nil, at: NSEvent.mouseLocation, in: nil)
        model.refresh()
        if kind == .teach { keepFocusOnClose = false }
    }

    // MARK: - External updates

    func setState(_ state: IconState) {
        applyIcon(state)
        switch state {
        case .idle: model.phase = .idle
        case .recording: model.phase = .recording
        case .transcribing: model.phase = .transcribing
        case .error: model.phase = .error
        }
    }

    private func applyIcon(_ state: IconState) {
        guard let button = statusItem.button else { return }
        button.image = UITheme.symbolImage(state.symbol)
        button.title = ""
        button.contentTintColor = state.tint
        button.toolTip = state.tooltip
    }

    /// daemon 回報的辨識模式（nil＝舊 daemon／沒有模式，選單隱藏）
    func setAsrMode(_ mode: String?) {
        asrMode = mode
        model.mode = mode
        asrModeMenuItem.isHidden = (mode == nil)
        asrFastItem.state = mode == "fast" ? .on : .off
        asrAccurateItem.state = mode == "accurate" ? .on : .off
        asrModeMenuItem.title = mode == "fast" ? "辨識模式（快）" : (mode == "accurate" ? "辨識模式（準）" : "辨識模式")
        punctMenuItem.title = mode == "fast" ? "標點模式（快：用模型自帶標點，原樣除外）" : "標點模式"
    }

    func setDaemonStatus(_ text: String) {
        daemonStatusMenuItem.title = "daemon：\(text)"
        model.daemonDetail = text
        if text.contains("線上") { model.daemonOnline = true }
        else if text.contains("離線") || text.contains("連不上") || text.contains("異常") { model.daemonOnline = false; model.mode = nil }
        else { model.daemonOnline = nil }
        if text.contains("離線") || text.contains("連不上") {
            daemonStatusMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.offline)
        } else if text.contains("線上") {
            daemonStatusMenuItem.image = UITheme.menuSymbol(UITheme.Symbol.success)
        } else {
            daemonStatusMenuItem.image = nil
        }
    }

    func refreshStats() {
        let stats = DictationStats.loadToday()
        statsMenuItem.title = stats.summaryLine
        model.stats = stats
    }

    /// 殼端 timeout 但 daemon 其實跑完時的復原：lastText 只在收到 socket 回應時更新，
    /// 開選單時額外核對 log 檔最後一筆成功記錄，避免「複製上一句」拿不到剛才其實成功的那句
    /// （2026-07-16 事故：daemon 完成但殼已判定逾時，in-memory lastText 停在更早那句）。
    private func recoverLastFromLogIfNeeded() {
        guard let entry = DictationStats.lastSuccessEntry(), entry.ts != lastAppliedLogTs else { return }
        lastAppliedLogTs = entry.ts
        guard entry.text != lastText else { return }
        setSuccess(text: entry.text, raw: entry.raw, latencyMs: entry.totalMs, changes: entry.changes)
        pushHistory(text: entry.text, raw: entry.raw, changes: entry.changes, latencyMs: entry.totalMs, at: entry.date)
    }

    func setLastResult(_ text: String?) {
        if let text, text.hasPrefix("⚠️") { model.notice = String(text.dropFirst(2)).trimmingCharacters(in: .whitespaces) }
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
        model.notice = nil
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

    func pushHistory(text: String, raw: String?, changes: [[String]] = [], latencyMs: Int? = nil, at: Date = Date()) {
        model.push(.init(text: text, raw: raw, changes: changes, latencyMs: latencyMs, at: at))
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

    @objc private func asrModeClicked(_ sender: NSMenuItem) {
        guard let mode = sender.representedObject as? String, mode != asrMode else { return }
        onSetAsrMode?(mode)
    }

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
