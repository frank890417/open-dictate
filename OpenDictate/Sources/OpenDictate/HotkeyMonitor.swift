import AppKit

/// 全域 push-to-talk 熱鍵監聽（fn 或 右 Option）。
///
/// - fn：`.flagsChanged` + keyCode 63（kVK_Function）
/// - 右 Option：keyCode 61（kVK_RightOption）
/// - global monitor 需要輔助使用；另掛 local 涵蓋自己 app 前景。
/// - 按住熱鍵時又按了別的鍵（fn＋方向鍵、fn＋E 表情、🌐 快捷鍵…）＝不是要講話 → onCancel，放開也不送。
///   一個月的 log：誤觸 75/899 次，筆電內建鍵盤＋麥克風時 45%（v0.8.0）。
final class HotkeyMonitor {
    enum Kind: String {
        case fn
        case rightOption

        var keyCode: UInt16 {
            switch self {
            case .fn: return 63          // kVK_Function
            case .rightOption: return 61 // kVK_RightOption
            }
        }

        var label: String {
            switch self {
            case .fn: return "fn"
            case .rightOption: return "右 Option"
            }
        }

        static func fromSettings() -> Kind {
            Kind(rawValue: DictateSettings.hotkey) ?? .fn
        }
    }

    var onDown: (() -> Void)?
    var onUp: (() -> Void)?
    var onCancel: (() -> Void)?

    private var kind: Kind = .fn
    private var globalMonitor: Any?
    private var localMonitor: Any?
    private var globalKeyMonitor: Any?
    private var localKeyMonitor: Any?
    private var isDown = false

    func start(kind: Kind = .fromSettings()) {
        stop()
        self.kind = kind
        globalMonitor = NSEvent.addGlobalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handle(event)
        }
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: .flagsChanged) { [weak self] event in
            self?.handle(event)
            return event
        }
        globalKeyMonitor = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] _ in
            self?.otherKeyPressed("按鍵")
        }
        localKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            self?.otherKeyPressed("按鍵")
            return event
        }
        mdLog("熱鍵監聽啟動：\(kind.label)（global \(globalMonitor == nil ? "掛載失敗" : "OK")）")
    }

    /// 設定變更時熱切換，不重啟 app。
    func reconfigure() {
        start(kind: .fromSettings())
    }

    func stop() {
        if let m = globalMonitor { NSEvent.removeMonitor(m); globalMonitor = nil }
        if let m = localMonitor { NSEvent.removeMonitor(m); localMonitor = nil }
        if let m = globalKeyMonitor { NSEvent.removeMonitor(m); globalKeyMonitor = nil }
        if let m = localKeyMonitor { NSEvent.removeMonitor(m); localKeyMonitor = nil }
        isDown = false
    }

    /// 熱鍵按住期間有別的鍵 → 取消這一次（放開熱鍵時不再觸發 onUp）。
    private func otherKeyPressed(_ what: String) {
        guard isDown else { return }
        isDown = false
        mdLog("按住 \(kind.label) 時按了其他\(what) → 取消這次錄音")
        onCancel?()
    }

    private func handle(_ event: NSEvent) {
        guard event.keyCode == kind.keyCode else {
            // 按住熱鍵時動了別的修飾鍵（fn＋Control＋F 之類的系統快捷鍵）也算組合鍵
            if isDown { otherKeyPressed("修飾鍵") }
            return
        }
        let down: Bool
        switch kind {
        case .fn:
            down = event.modifierFlags.contains(.function)
        case .rightOption:
            down = event.modifierFlags.contains(.option)
        }
        if down && !isDown {
            isDown = true
            onDown?()
        } else if !down && isDown {
            isDown = false
            onUp?()
        }
    }

    deinit { stop() }
}
