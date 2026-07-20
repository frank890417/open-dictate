import AppKit
import ApplicationServices
import Carbon.HIToolbox

/// 把文字注入游標位置。
///
/// 模式（DictateSettings.injectMode）：
/// - auto：先試 AX 在 focused 元件插入／寫入，失敗再 Cmd-V
/// - ax：只走 AX（失敗則字留剪貼簿 + 通知）
/// - paste：只走剪貼簿 + Cmd-V（舊行為）
///
/// CGEvent / AX 都需要輔助使用授權。
enum TextInjector {
    private static let transientType = NSPasteboard.PasteboardType("org.nspasteboard.TransientType")

    enum Method: String {
        case axInsert, axSetValue, paste, clipboardOnly
    }

    /// main thread 呼叫。completion 在注入完成後於 main thread 回。
    static func inject(_ text: String, completion: @escaping (Method) -> Void = { _ in }) {
        guard PermissionGuide.isAccessibilityTrusted else {
            leaveOnClipboard(text)
            mdLog("⚠️ 輔助使用未授權：字已留剪貼簿")
            Notifier.notify(title: "字在剪貼簿，請手動 Cmd-V",
                            body: "輔助使用未授權。系統設定 > 隱私權與安全性 > 輔助使用 → OpenDictate。")
            completion(.clipboardOnly)
            return
        }

        let mode = DictateSettings.injectMode
        switch mode {
        case "paste":
            pasteViaClipboard(text) { completion(.paste) }
        case "ax":
            if let m = tryAX(text) {
                completion(m)
            } else {
                leaveOnClipboard(text)
                Notifier.notify(title: "AX 注入失敗，字在剪貼簿", body: "此 app 不支援無障礙寫入，請 Cmd-V。")
                completion(.clipboardOnly)
            }
        default: // auto
            if let m = tryAX(text) {
                mdLog("注入：\(m.rawValue)")
                completion(m)
            } else {
                mdLog("注入：AX 失敗 → paste fallback")
                pasteViaClipboard(text) { completion(.paste) }
            }
        }
    }

    // MARK: - AX

    /// 嘗試在 focused UI element 插入文字。成功回 method，失敗 nil。
    private static func tryAX(_ text: String) -> Method? {
        let system = AXUIElementCreateSystemWide()
        var focusedObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focusedObj) == .success,
              let focusedRef = focusedObj, CFGetTypeID(focusedRef) == AXUIElementGetTypeID() else {
            return nil
        }
        let el = focusedRef as! AXUIElement

        // 只對明顯可編輯角色動手（兩段共用）。
        // Chromium/Electron 的 WebArea 會對 AX 寫入回報 success 但實際丟棄 →
        // 沒有這個閘門，auto 模式會「轉錄成功、字蒸發、剪貼簿也沒有」（2026-07-11 實案）。
        var roleObj: CFTypeRef?
        _ = AXUIElementCopyAttributeValue(el, kAXRoleAttribute as CFString, &roleObj)
        let role = (roleObj as? String) ?? ""
        let editableRoles: Set<String> = [
            "AXTextField", "AXTextArea", "AXComboBox", "AXSearchField",
            kAXTextFieldRole as String, kAXTextAreaRole as String, kAXComboBoxRole as String
        ]
        guard editableRoles.contains(role) else { return nil }

        // 1) 有選取範圍：用 kAXSelectedTextAttribute 寫入（替換選取 / 插入游標處，多數文字欄支援）
        var rangeObj: CFTypeRef?
        if AXUIElementCopyAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, &rangeObj) == .success,
           rangeObj != nil {
            let err = AXUIElementSetAttributeValue(el, kAXSelectedTextAttribute as CFString, text as CFTypeRef)
            if err == .success {
                return .axInsert
            }
        }

        // 2) 可設定 value：讀舊值 + 選取 range 手動拼接後 set value

        var valueObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXValueAttribute as CFString, &valueObj) == .success,
              let old = valueObj as? String else { return nil }

        var insertAt = old.count
        var replaceLen = 0
        if let rangeObj,
           CFGetTypeID(rangeObj) == AXValueGetTypeID() {
            var range = CFRange()
            if AXValueGetValue(rangeObj as! AXValue, .cfRange, &range) {
                insertAt = max(0, min(range.location, old.count))
                replaceLen = max(0, min(range.length, old.count - insertAt))
            }
        }

        let start = old.index(old.startIndex, offsetBy: insertAt)
        let end = old.index(start, offsetBy: replaceLen)
        let newVal = String(old[..<start]) + text + String(old[end...])
        let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, newVal as CFTypeRef)
        if err == .success {
            // 盡量把游標移到插入後
            var newRange = CFRange(location: insertAt + text.count, length: 0)
            if let rv = AXValueCreate(.cfRange, &newRange) {
                _ = AXUIElementSetAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, rv)
            }
            return .axSetValue
        }
        return nil
    }

    // MARK: - Pasteboard + Cmd-V

    private static func pasteViaClipboard(_ text: String, completion: @escaping () -> Void) {
        let pasteboard = NSPasteboard.general
        let saved = snapshot(pasteboard)

        let item = NSPasteboardItem()
        item.setString(text, forType: .string)
        item.setString("", forType: transientType)
        pasteboard.clearContents()
        pasteboard.writeObjects([item])

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.10) {
            postCmdV()
            // 慢 app（Electron/瀏覽器忙碌時）吃貼上要時間；太早還原 = 貼到舊剪貼簿內容
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.40) {
                restore(pasteboard, items: saved)
                completion()
            }
        }
    }

    private static func leaveOnClipboard(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(text, forType: .string)
    }

    private static func snapshot(_ pasteboard: NSPasteboard) -> [[NSPasteboard.PasteboardType: Data]] {
        (pasteboard.pasteboardItems ?? []).map { item in
            var copy: [NSPasteboard.PasteboardType: Data] = [:]
            for type in item.types {
                if let data = item.data(forType: type) { copy[type] = data }
            }
            return copy
        }
    }

    private static func restore(_ pasteboard: NSPasteboard, items: [[NSPasteboard.PasteboardType: Data]]) {
        pasteboard.clearContents()
        guard !items.isEmpty else { return }
        let restored: [NSPasteboardItem] = items.map { dict in
            let item = NSPasteboardItem()
            for (type, data) in dict { item.setData(data, forType: type) }
            return item
        }
        pasteboard.writeObjects(restored)
    }

    private static func postCmdV() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let vKey = CGKeyCode(kVK_ANSI_V)
        guard let keyDown = CGEvent(keyboardEventSource: source, virtualKey: vKey, keyDown: true),
              let keyUp = CGEvent(keyboardEventSource: source, virtualKey: vKey, keyDown: false) else {
            mdLog("CGEvent 建立失敗（Cmd-V 沒送出）")
            return
        }
        keyDown.flags = .maskCommand
        keyUp.flags = .maskCommand
        keyDown.post(tap: .cgSessionEventTap)
        keyUp.post(tap: .cgSessionEventTap)
    }
}
