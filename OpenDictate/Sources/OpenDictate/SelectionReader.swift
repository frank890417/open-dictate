import AppKit
import ApplicationServices

/// 讀取目前焦點元件的選取文字（教詞庫用）。
enum SelectionReader {

    /// 有選取 → 回傳字串；無選取 / 無 AX → nil。
    static func selectedText() -> String? {
        let system = AXUIElementCreateSystemWide()
        var focusedObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focusedObj) == .success,
              let focusedRef = focusedObj, CFGetTypeID(focusedRef) == AXUIElementGetTypeID() else {
            return nil
        }
        let el = focusedRef as! AXUIElement

        var selectedObj: CFTypeRef?
        if AXUIElementCopyAttributeValue(el, kAXSelectedTextAttribute as CFString, &selectedObj) == .success,
           let s = selectedObj as? String {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty { return t }
        }
        return nil
    }

    /// 剪貼簿字串（使用者可先 Cmd-C 再教）。
    static func clipboardText() -> String? {
        let s = NSPasteboard.general.string(forType: .string)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let s, !s.isEmpty else { return nil }
        return s
    }
}
