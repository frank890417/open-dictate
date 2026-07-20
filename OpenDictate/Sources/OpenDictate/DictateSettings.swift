import Foundation

/// 使用者偏好（UserDefaults；menu bar / 設定窗即時切換，無需重啟）。
enum DictateSettings {
    private static let d = UserDefaults.standard

    // MARK: - HUD / 介面

    /// 錄音浮動提示（HUD：游標旁波形 + 成敗回饋）
    static var hudEnabled: Bool {
        get { d.object(forKey: "hudEnabled") as? Bool ?? true }
        set { d.set(newValue, forKey: "hudEnabled") }
    }

    /// 成功時若 raw ≠ text，HUD 顯示對照
    static var showRawDiff: Bool {
        get { d.object(forKey: "showRawDiff") as? Bool ?? true }
        set { d.set(newValue, forKey: "showRawDiff") }
    }

    // MARK: - 標點

    /// smart_zh / llm_zh / raw
    static var punctMode: String {
        get {
            if let v = d.string(forKey: "punctMode") { return v }
            if let old = d.object(forKey: "punctSmartZh") as? Bool { return old ? "smart_zh" : "raw" }
            return "smart_zh"
        }
        set { d.set(newValue, forKey: "punctMode") }
    }

    static var punctModeLabel: String {
        switch punctMode {
        case "llm_zh": return "LLM 標點"
        case "raw": return "原樣"
        default: return "智慧全形"
        }
    }

    // MARK: - 熱鍵

    /// fn | rightOption
    static var hotkey: String {
        get { d.string(forKey: "hotkey") ?? "fn" }
        set { d.set(newValue, forKey: "hotkey") }
    }

    static var hotkeyLabel: String {
        switch hotkey {
        case "rightOption": return "右 Option"
        default: return "fn"
        }
    }

    // MARK: - 注入

    /// paste | auto（先 AX 再 Cmd-V）| ax
    /// 預設 paste（v0.4 前的已驗證行為）。auto 的 AX 直寫在 Electron/WebArea 會假成功吃字
    /// （2026-07-11 實案），加角色閘門後仍屬 opt-in。
    static var injectMode: String {
        get { d.string(forKey: "injectMode") ?? "paste" }
        set { d.set(newValue, forKey: "injectMode") }
    }

    static var injectModeLabel: String {
        switch injectMode {
        case "paste": return "剪貼簿 Cmd-V"
        case "ax": return "僅 AX 直寫"
        default: return "自動（AX → 貼上）"
        }
    }

    // MARK: - 麥克風

    /// AVAudioDevice uniqueID；nil / "" = 系統預設
    static var preferredMicUID: String? {
        get {
            let v = d.string(forKey: "preferredMicUID") ?? ""
            return v.isEmpty ? nil : v
        }
        set { d.set(newValue ?? "", forKey: "preferredMicUID") }
    }
}
