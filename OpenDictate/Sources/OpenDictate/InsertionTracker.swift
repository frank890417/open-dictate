import AppKit

/// 最近一次打進別的 app 的那句（v0.9 撤回／重轉用）：打在哪個 app、之後使用者有沒有再碰鍵盤或滑鼠。
/// 只記這幾件事、不讀那個 app 的內容；換字前的內容核對在 TextInjector.replaceLast（AX 讀得到時）。
final class InsertionTracker {
    static let shared = InsertionTracker()

    private(set) var text: String?
    private(set) var at: Date?
    /// 使用者最後一次在別的 app 打字或點滑鼠（晚到的句子：放開熱鍵之後有接著打字就不插進去）
    private(set) var lastUserInputAt: Date?
    private var pid: pid_t?
    private var touched = false
    private var monitor: Any?

    /// 全域監聽（要輔助使用）：只看得到送往別的 app 的事件，點選單列圖示、控制中心不算。
    func start() {
        guard monitor == nil else { return }
        monitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.keyDown, .leftMouseDown, .rightMouseDown, .otherMouseDown]) { [weak self] event in
            guard let self, !TextInjector.isSynthetic(event) else { return }
            self.lastUserInputAt = Date()
            if self.text != nil { self.touched = true }
        }
    }

    func record(_ text: String) {
        self.text = text
        pid = NSWorkspace.shared.frontmostApplication?.processIdentifier
        at = Date()
        touched = false
    }

    func clear() {
        text = nil
        pid = nil
        at = nil
        touched = false
    }

    /// 還在同一個 app、打進去之後沒有打字也沒有點滑鼠 → 游標應該還緊接在那句後面
    var isUntouched: Bool {
        guard text != nil, !touched, let pid else { return false }
        return NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
    }
}
