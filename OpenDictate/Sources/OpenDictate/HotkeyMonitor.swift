import AppKit

/// 全域 push-to-talk 熱鍵監聽（fn 或 右 Option）。
///
/// - fn：`.flagsChanged` + keyCode 63（kVK_Function）
/// - 右 Option：keyCode 61（kVK_RightOption）
/// - global monitor 需要輔助使用；另掛 local 涵蓋自己 app 前景。
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

    private var kind: Kind = .fn
    private var globalMonitor: Any?
    private var localMonitor: Any?
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
        mdLog("熱鍵監聽啟動：\(kind.label)（global \(globalMonitor == nil ? "掛載失敗" : "OK")）")
    }

    /// 設定變更時熱切換，不重啟 app。
    func reconfigure() {
        start(kind: .fromSettings())
    }

    func stop() {
        if let m = globalMonitor { NSEvent.removeMonitor(m); globalMonitor = nil }
        if let m = localMonitor { NSEvent.removeMonitor(m); localMonitor = nil }
        isDown = false
    }

    private func handle(_ event: NSEvent) {
        guard event.keyCode == kind.keyCode else { return }
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
