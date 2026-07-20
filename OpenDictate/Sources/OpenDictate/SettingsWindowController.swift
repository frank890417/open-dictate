import AppKit

/// 完整偏好設定視窗。
final class SettingsWindowController: NSWindowController {

    var onReloadLexicon: (() -> Void)?
    var onRestartDaemon: (() -> Void)?
    var onOpenLog: (() -> Void)?
    var onCheckDaemon: (() -> Void)?
    var onHotkeyChanged: (() -> Void)?
    var onMicChanged: (() -> Void)?

    private let daemonLabel = NSTextField(labelWithString: "daemon：—")
    private let modelLabel = NSTextField(labelWithString: "model：—")
    private let statsLabel = NSTextField(wrappingLabelWithString: "")
    private let punctPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let hotkeyPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let injectPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let micPopup = NSPopUpButton(frame: .zero, pullsDown: false)
    private let hudCheck = NSButton(checkboxWithTitle: "錄音時顯示游標旁浮動提示（HUD）", target: nil, action: nil)
    private let rawDiffCheck = NSButton(checkboxWithTitle: "成功時顯示 raw／詞庫對照", target: nil, action: nil)
    private var micUIDs: [String] = [""] // index 對應 popup；"" = 系統預設

    convenience init() {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 520),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        win.title = "\(ProductConfig.appName) 設定"
        win.isReleasedWhenClosed = false
        win.center()
        self.init(window: win)
        win.contentView = buildContent()
        reloadFromSettings()
    }

    func show() {
        reloadFromSettings()
        refreshStats()
        onCheckDaemon?()
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setDaemonStatus(_ text: String, model: String? = nil) {
        daemonLabel.stringValue = "daemon：\(text)"
        if let model, !model.isEmpty {
            modelLabel.stringValue = "model：\(model)"
        }
    }

    func refreshStats() {
        statsLabel.stringValue = DictationStats.loadToday().detailBlock
    }

    // MARK: - UI

    private func buildContent() -> NSView {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 460, height: 520))
        let scroll = NSScrollView(frame: root.bounds)
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        scroll.borderType = .noBorder
        scroll.drawsBackground = false

        let doc = NSView(frame: NSRect(x: 0, y: 0, width: 460, height: 500))
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        doc.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: doc.topAnchor, constant: 20),
            stack.leadingAnchor.constraint(equalTo: doc.leadingAnchor, constant: 22),
            stack.trailingAnchor.constraint(equalTo: doc.trailingAnchor, constant: -22),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: doc.bottomAnchor, constant: -20),
        ])

        // 狀態
        stack.addArrangedSubview(sectionTitle("狀態"))
        daemonLabel.font = .systemFont(ofSize: 13)
        modelLabel.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        modelLabel.textColor = .secondaryLabelColor
        stack.addArrangedSubview(daemonLabel)
        stack.addArrangedSubview(modelLabel)

        stack.addArrangedSubview(sectionTitle("今日統計"))
        statsLabel.font = .systemFont(ofSize: 12)
        statsLabel.textColor = .secondaryLabelColor
        statsLabel.preferredMaxLayoutWidth = 400
        stack.addArrangedSubview(statsLabel)

        // 標點
        stack.addArrangedSubview(sectionTitle("標點模式"))
        punctPopup.addItems(withTitles: [
            "智慧全形（規則層，快）",
            "LLM 完整標點（頓號引號，+1 秒）",
            "原樣（whisper 直出）"
        ])
        punctPopup.target = self
        punctPopup.action = #selector(punctChanged)
        constrainWidth(punctPopup, 360)
        stack.addArrangedSubview(punctPopup)

        // 熱鍵
        stack.addArrangedSubview(sectionTitle("熱鍵（按住講話）"))
        hotkeyPopup.addItems(withTitles: ["fn", "右 Option"])
        hotkeyPopup.target = self
        hotkeyPopup.action = #selector(hotkeyChanged)
        constrainWidth(hotkeyPopup, 200)
        stack.addArrangedSubview(hotkeyPopup)
        stack.addArrangedSubview(hint("選 fn 時請在系統設定把 🌐 設為「不執行任何操作」，並關閉系統聽寫快捷鍵。"))

        // 麥克風
        stack.addArrangedSubview(sectionTitle("麥克風"))
        micPopup.target = self
        micPopup.action = #selector(micChanged)
        constrainWidth(micPopup, 360)
        stack.addArrangedSubview(micPopup)

        // 注入
        stack.addArrangedSubview(sectionTitle("文字注入"))
        injectPopup.addItems(withTitles: [
            "自動（先 AX 直寫，失敗再 Cmd-V）",
            "僅 AX 直寫",
            "剪貼簿 Cmd-V"
        ])
        injectPopup.target = self
        injectPopup.action = #selector(injectChanged)
        constrainWidth(injectPopup, 360)
        stack.addArrangedSubview(injectPopup)
        stack.addArrangedSubview(hint("自動模式在多數文字欄較乾淨；終端機 / 特殊 app 會 fallback 貼上。"))

        // HUD
        stack.addArrangedSubview(sectionTitle("介面"))
        hudCheck.target = self
        hudCheck.action = #selector(hudChanged)
        rawDiffCheck.target = self
        rawDiffCheck.action = #selector(rawDiffChanged)
        stack.addArrangedSubview(hudCheck)
        stack.addArrangedSubview(rawDiffCheck)

        // 維護
        stack.addArrangedSubview(sectionTitle("維護"))
        let row = NSStackView()
        row.orientation = .horizontal
        row.spacing = 8
        row.addArrangedSubview(makeButton("重載詞庫", #selector(reloadClicked)))
        row.addArrangedSubview(makeButton("重啟 daemon", #selector(restartClicked)))
        row.addArrangedSubview(makeButton("今日記錄", #selector(logClicked)))
        row.addArrangedSubview(makeButton("權限…", #selector(permClicked)))
        stack.addArrangedSubview(row)

        let version = NSTextField(labelWithString: "\(ProductConfig.appName) v0.5 · 本地 MLX Whisper · 語音不出境 · 確定性詞庫")
        version.font = .systemFont(ofSize: 10)
        version.textColor = .tertiaryLabelColor
        stack.addArrangedSubview(version)

        scroll.documentView = doc
        // 固定 doc 高度足夠
        doc.setFrameSize(NSSize(width: 460, height: 560))
        root.addSubview(scroll)
        return root
    }

    private func sectionTitle(_ t: String) -> NSTextField {
        let l = NSTextField(labelWithString: t)
        l.font = .systemFont(ofSize: 11, weight: .semibold)
        l.textColor = .secondaryLabelColor
        return l
    }

    private func hint(_ t: String) -> NSTextField {
        let l = NSTextField(wrappingLabelWithString: t)
        l.font = .systemFont(ofSize: 11)
        l.textColor = .secondaryLabelColor
        l.preferredMaxLayoutWidth = 400
        return l
    }

    private func makeButton(_ title: String, _ sel: Selector) -> NSButton {
        let b = NSButton(title: title, target: self, action: sel)
        b.bezelStyle = .rounded
        return b
    }

    private func constrainWidth(_ view: NSView, _ w: CGFloat) {
        view.translatesAutoresizingMaskIntoConstraints = false
        view.widthAnchor.constraint(equalToConstant: w).isActive = true
    }

    private func reloadFromSettings() {
        switch DictateSettings.punctMode {
        case "llm_zh": punctPopup.selectItem(at: 1)
        case "raw": punctPopup.selectItem(at: 2)
        default: punctPopup.selectItem(at: 0)
        }
        hotkeyPopup.selectItem(at: DictateSettings.hotkey == "rightOption" ? 1 : 0)
        switch DictateSettings.injectMode {
        case "ax": injectPopup.selectItem(at: 1)
        case "paste": injectPopup.selectItem(at: 2)
        default: injectPopup.selectItem(at: 0)
        }
        hudCheck.state = DictateSettings.hudEnabled ? .on : .off
        rawDiffCheck.state = DictateSettings.showRawDiff ? .on : .off
        rebuildMicPopup()
    }

    private func rebuildMicPopup() {
        micPopup.removeAllItems()
        micUIDs = [""]
        micPopup.addItem(withTitle: "系統預設")
        let preferred = DictateSettings.preferredMicUID
        var select = 0
        for (i, dev) in MicDevice.listInputs().enumerated() {
            let title = dev.isDefault ? "\(dev.name)（系統預設裝置）" : dev.name
            micPopup.addItem(withTitle: title)
            micUIDs.append(dev.uid)
            if preferred == dev.uid { select = i + 1 }
        }
        if preferred == nil { select = 0 }
        micPopup.selectItem(at: select)
    }

    @objc private func punctChanged() {
        switch punctPopup.indexOfSelectedItem {
        case 1: DictateSettings.punctMode = "llm_zh"
        case 2: DictateSettings.punctMode = "raw"
        default: DictateSettings.punctMode = "smart_zh"
        }
    }

    @objc private func hotkeyChanged() {
        DictateSettings.hotkey = hotkeyPopup.indexOfSelectedItem == 1 ? "rightOption" : "fn"
        onHotkeyChanged?()
    }

    @objc private func injectChanged() {
        switch injectPopup.indexOfSelectedItem {
        case 1: DictateSettings.injectMode = "ax"
        case 2: DictateSettings.injectMode = "paste"
        default: DictateSettings.injectMode = "auto"
        }
    }

    @objc private func micChanged() {
        let i = micPopup.indexOfSelectedItem
        let uid = (i >= 0 && i < micUIDs.count) ? micUIDs[i] : ""
        DictateSettings.preferredMicUID = uid.isEmpty ? nil : uid
        onMicChanged?()
    }

    @objc private func hudChanged() { DictateSettings.hudEnabled = hudCheck.state == .on }
    @objc private func rawDiffChanged() { DictateSettings.showRawDiff = rawDiffCheck.state == .on }

    @objc private func reloadClicked() { onReloadLexicon?() }
    @objc private func restartClicked() { onRestartDaemon?() }
    @objc private func logClicked() { onOpenLog?() }
    @objc private func permClicked() { PermissionGuide.showGuideAlert() }
}
