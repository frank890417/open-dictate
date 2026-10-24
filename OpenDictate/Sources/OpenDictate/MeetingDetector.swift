import AppKit
import CoreAudio

/// 偵測「正在開會」→ 提醒一次要不要開會議錄音（ProductConfig.meetingURL）。
///
/// 訊號＝哪個 App 正在用音訊輸入（CoreAudio process 物件，macOS 14.4+），不是「麥克風忙不忙」：
/// OBS 直播課、TouchDesigner 的音訊輸入、Claude 語音模式也會一直佔著同一支麥克風，只看裝置會一直誤報。
/// 只有會議類 App（下面的清單，前綴比對，所以瀏覽器／Electron 的 helper 行程也算）連續用輸入 ≥20 秒才問；
/// 同一場（那個 App 沒有放掉輸入超過 60 秒）只問一次，按「不用」也算問過。會議 App 已經在錄（狀態檔存在）就不問。
final class MeetingDetector {

    /// 會議類 App：bundle id 前綴 → 顯示名稱
    static let meetingApps: [(prefix: String, name: String)] = [
        ("us.zoom.xos", "Zoom"), ("com.microsoft.teams2", "Teams"), ("com.microsoft.teams", "Teams"), ("com.cisco.webexmeetingsapp", "Webex"),
        ("Cisco-Systems.Spark", "Webex"), ("com.apple.FaceTime", "FaceTime"),
        ("com.google.Chrome", "Chrome"), ("com.apple.Safari", "Safari"), ("com.apple.WebKit", "Safari"),
        ("company.thebrowser.Browser", "Arc"), ("com.microsoft.edgemac", "Edge"), ("org.mozilla.firefox", "Firefox"),
        ("com.brave.Browser", "Brave"), ("com.tinyspeck.slackmacgap", "Slack"), ("com.hnc.Discord", "Discord"),
        ("jp.naver.line.mac", "LINE"), ("net.whatsapp.WhatsApp", "WhatsApp"), ("ru.keepcoder.Telegram", "Telegram"),
    ]

    /// 這個 bundle id 是不是會議類 App；是就回顯示名稱。
    static func meetingAppName(for bundleID: String) -> String? {
        meetingApps.first { bundleID == $0.prefix || bundleID.hasPrefix($0.prefix + ".") }?.name
    }

    struct InputClient: Equatable {
        let bundleID: String
        let pid: pid_t
    }

    /// 現在正在用音訊輸入的行程（不含自己）。nil＝這台 macOS 沒有 process 物件（< 14.4）或讀不到。
    static func inputClients() -> [InputClient]? {
        guard #available(macOS 14.4, *) else { return nil }
        let system = AudioObjectID(kAudioObjectSystemObject)
        var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                              mScope: kAudioObjectPropertyScopeGlobal,
                                              mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr else { return nil }
        var objects = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard !objects.isEmpty,
              AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &objects) == noErr else { return [] }
        let me = ProcessInfo.processInfo.processIdentifier
        var out: [InputClient] = []
        for obj in objects {
            var running: UInt32 = 0
            var rsize = UInt32(MemoryLayout<UInt32>.size)
            var a = AudioObjectPropertyAddress(mSelector: kAudioProcessPropertyIsRunningInput,
                                               mScope: kAudioObjectPropertyScopeGlobal,
                                               mElement: kAudioObjectPropertyElementMain)
            guard AudioObjectGetPropertyData(obj, &a, 0, nil, &rsize, &running) == noErr, running != 0 else { continue }
            var pid: pid_t = 0
            var psize = UInt32(MemoryLayout<pid_t>.size)
            a.mSelector = kAudioProcessPropertyPID
            _ = AudioObjectGetPropertyData(obj, &a, 0, nil, &psize, &pid)
            if pid == me { continue }
            var bundle: Unmanaged<CFString>?
            var bsize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            a.mSelector = kAudioProcessPropertyBundleID
            var id = ""
            if AudioObjectGetPropertyData(obj, &a, 0, nil, &bsize, &bundle) == noErr, let b = bundle {
                id = b.takeRetainedValue() as String
            }
            if id.isEmpty { id = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier ?? "" }
            out.append(InputClient(bundleID: id, pid: pid))
        }
        return out
    }

    /// 純邏輯（可測）：每次取樣餵「正在用輸入的會議 App 名稱」，回這一刻要不要提醒、提醒誰。
    struct Debouncer {
        var sustain: TimeInterval = 20       // 連續用多久才算開會（點開設定測麥克風不算）
        var gapReset: TimeInterval = 60      // 放掉輸入超過這麼久＝下一場
        private var since: [String: Date] = [:]
        private var lastSeen: [String: Date] = [:]
        private var asked: Set<String> = []

        mutating func feed(_ apps: Set<String>, at now: Date) -> String? {
            for app in apps {
                if let seen = lastSeen[app], now.timeIntervalSince(seen) <= gapReset {
                    // 同一場繼續
                } else {
                    since[app] = now
                    asked.remove(app)
                }
                lastSeen[app] = now
            }
            for (app, seen) in lastSeen where !apps.contains(app) && now.timeIntervalSince(seen) > gapReset {
                lastSeen[app] = nil
                since[app] = nil
                asked.remove(app)
            }
            let due = apps.filter { !asked.contains($0) && now.timeIntervalSince(since[$0] ?? now) >= sustain }
            guard let app = due.min(by: { (since[$0] ?? now) < (since[$1] ?? now) }) else { return nil }
            asked.insert(app)
            return app
        }

        /// 正在通話（已經到 sustain）的會議 App；控制中心「偵測到 Zoom 正在通話」用
        func active(at now: Date) -> String? {
            // 最近 15 秒內還在用，而且實際連續用了 ≥ sustain（不是「從開始到現在」：用 15 秒就放掉的不算）
            since.filter { app, t0 in
                guard let seen = lastSeen[app] else { return false }
                return now.timeIntervalSince(seen) <= 15 && seen.timeIntervalSince(t0) >= sustain
            }.min { $0.value < $1.value }?.key
        }
    }

    var onPrompt: ((String) -> Void)?
    /// 正在通話的會議 App 有變（nil＝沒有）
    var onActiveChanged: ((String?) -> Void)?

    private var debouncer = Debouncer()
    private var timer: Timer?
    private var lastActive: String?

    func start() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.tick() }
        mdLog("會議偵測啟動（每 5 秒看誰在用音訊輸入）")
    }

    func stop() {
        timer?.invalidate()
        timer = nil
        if lastActive != nil { lastActive = nil; onActiveChanged?(nil) }
    }

    private func tick() {
        guard let clients = Self.inputClients() else { stop(); return }      // 舊系統：沒有訊號就不猜
        let apps = Set(clients.compactMap { Self.meetingAppName(for: $0.bundleID) })
        let now = Date()
        let recording = ProductConfig.meetingStatusFile.map { FileManager.default.fileExists(atPath: $0) } ?? false
        let prompt = debouncer.feed(apps, at: now)
        let active = recording ? nil : debouncer.active(at: now)
        if active != lastActive {
            lastActive = active
            onActiveChanged?(active)
        }
        if let app = prompt, !recording {
            mdLog("偵測到 \(app) 正在用麥克風 ≥\(Int(debouncer.sustain)) 秒 → 提醒會議錄音")
            onPrompt?(app)
        }
    }
}

/// 「要開始會議錄音嗎？」小面板：右上角、不搶焦點、可以直接點（通知權限常被拒，系統通知看不到）。
final class MeetingPromptPanel {
    private var panel: NSPanel?
    private var hideWork: DispatchWorkItem?

    static let size = NSSize(width: 300, height: 76)

    /// 面板內容（`--probe-meeting-prompt` 截圖用同一份）
    static func makeContent(app: String, onStart: @escaping () -> Void, onDismiss: @escaping () -> Void) -> NSView {
        let size = Self.size
        let fx = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        fx.material = .hudWindow
        fx.state = .active
        fx.blendingMode = .behindWindow
        fx.wantsLayer = true
        fx.layer?.cornerRadius = UITheme.hudCorner
        fx.layer?.masksToBounds = true

        let icon = NSImageView(frame: NSRect(x: 12, y: 40, width: 22, height: 22))
        icon.image = UITheme.symbolImage("record.circle", pointSize: 17, weight: .semibold)
        icon.contentTintColor = .systemRed
        let title = NSTextField(labelWithString: "\(app) 正在通話")
        title.font = .systemFont(ofSize: 12.5, weight: .semibold)
        title.frame = NSRect(x: 42, y: 50, width: size.width - 54, height: 17)
        let sub = NSTextField(labelWithString: "要開始會議錄音嗎？逐字稿與重點會即時出來")
        sub.font = .systemFont(ofSize: 11)
        sub.textColor = .secondaryLabelColor
        sub.frame = NSRect(x: 42, y: 33, width: size.width - 54, height: 15)

        let start = FirstClickButton(title: "開始錄音", target: nil, action: nil)
        start.bezelStyle = .rounded
        start.keyEquivalent = ""
        start.contentTintColor = .systemRed
        start.frame = NSRect(x: size.width - 170, y: 6, width: 86, height: 26)
        start.onClick = onStart
        start.target = start
        start.action = #selector(FirstClickButton.fire)
        let no = FirstClickButton(title: "不用", target: nil, action: nil)
        no.bezelStyle = .rounded
        no.frame = NSRect(x: size.width - 80, y: 6, width: 68, height: 26)
        no.onClick = onDismiss
        no.target = no
        no.action = #selector(FirstClickButton.fire)

        [icon, title, sub, start, no].forEach(fx.addSubview)
        return fx
    }

    func show(app: String, onStart: @escaping () -> Void) {
        hide()
        let size = Self.size
        let p = NSPanel(contentRect: NSRect(origin: .zero, size: size),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: true)
        p.isFloatingPanel = true
        p.level = .statusBar
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = true
        p.hidesOnDeactivate = false
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]

        let fx = Self.makeContent(app: app, onStart: { [weak self] in self?.hide(); onStart() },
                                  onDismiss: { [weak self] in mdLog("會議提醒：這場不用"); self?.hide() })
        p.contentView = fx
        let screen = NSScreen.main ?? NSScreen.screens[0]
        let f = screen.visibleFrame
        p.setFrameOrigin(NSPoint(x: f.maxX - size.width - 12, y: f.maxY - size.height - 10))
        p.orderFrontRegardless()
        panel = p
        let work = DispatchWorkItem { [weak self] in self?.hide() }     // 一分鐘沒理就收起來（這場不會再問）
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 60, execute: work)
    }

    func hide() {
        hideWork?.cancel()
        hideWork = nil
        panel?.orderOut(nil)
        panel = nil
    }
}

/// 不在前景的面板上，第一下就要按得到（NSButton 預設第一下只是讓視窗變成 key）
final class FirstClickButton: NSButton {
    var onClick: (() -> Void)?
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    @objc func fire() { onClick?() }
}
