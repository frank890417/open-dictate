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

    /// 我們自己送出的按鍵（Cmd-V、倒退鍵）都蓋這個記號，熱鍵監聽與「貼上後有沒有再打字」才認得出不是使用者按的。
    /// v0.9 連講：上一句的 Cmd-V 可能在錄下一句時才送出，沒有記號就會把正在錄的那句當成組合鍵取消掉。
    static let syntheticMarker: Int64 = 0x4D44_4943   // "MDIC"

    static func isSynthetic(_ event: NSEvent) -> Bool {
        event.cgEvent?.getIntegerValueField(.eventSourceUserData) == syntheticMarker
    }

    /// main thread 呼叫。completion 在注入完成後於 main thread 回（貼上：Cmd-V 一送出就回，不等剪貼簿還原）。
    static func inject(_ text: String, completion: @escaping (Method) -> Void = { _ in }) {
        let done: (Method) -> Void = { method in
            if method != .clipboardOnly { InsertionTracker.shared.record(text) }
            completion(method)
        }
        guard PermissionGuide.isAccessibilityTrusted else {
            leaveOnClipboard(text)
            mdLog("⚠️ 輔助使用未授權：字已留剪貼簿")
            Notifier.notify(title: "字在剪貼簿，請手動 Cmd-V",
                            body: "輔助使用未授權。系統設定 > 隱私權與安全性 > 輔助使用 → \(ProductConfig.appName)。")
            done(.clipboardOnly)
            return
        }

        let mode = DictateSettings.injectMode
        switch mode {
        case "paste":
            pasteViaClipboard(text) { done(.paste) }
        case "ax":
            if let m = tryAX(text) {
                done(m)
            } else {
                leaveOnClipboard(text)
                Notifier.notify(title: "AX 注入失敗，字在剪貼簿", body: "此 app 不支援無障礙寫入，請 Cmd-V。")
                done(.clipboardOnly)
            }
        default: // auto
            if let m = tryAX(text) {
                mdLog("注入：\(m.rawValue)")
                done(m)
            } else {
                mdLog("注入：AX 失敗 → paste fallback")
                pasteViaClipboard(text) { done(.paste) }
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

    /// 剪貼簿一次只給一句用：寫入 → 0.1 秒後 Cmd-V → 再 0.4 秒（慢 app 讀完剪貼簿）才換下一句或還原。
    /// 連講時好幾句接著來：使用者原本的剪貼簿只在第一句前存一次、最後一句貼完才還原。
    private enum ClipJob {
        case paste(String, () -> Void)
        case leave(String)
    }
    private static var clipQueue: [ClipJob] = []
    private static var clipBusy = false
    private static var savedClipboard: [[NSPasteboard.PasteboardType: Data]]?

    /// completion 在 Cmd-V 送出當下就回：殼立刻回閒置、下一句可以馬上錄（v0.8 之前要再等還原，每句鎖 0.54 秒）。
    private static func pasteViaClipboard(_ text: String, completion: @escaping () -> Void) {
        clipQueue.append(.paste(text, completion))
        pumpClipboard()
    }

    /// 字留在剪貼簿、不貼。排在正在貼的那句後面，不然會被它的還原蓋掉。
    static func leaveOnClipboard(_ text: String) {
        clipQueue.append(.leave(text))
        pumpClipboard()
    }

    private static func pumpClipboard() {
        guard !clipBusy else { return }
        let pasteboard = NSPasteboard.general
        guard !clipQueue.isEmpty else {
            if let saved = savedClipboard {
                restore(pasteboard, items: saved)
                savedClipboard = nil
            }
            return
        }
        switch clipQueue.removeFirst() {
        case .leave(let text):
            savedClipboard = nil            // 刻意換掉剪貼簿：之後不還原
            pasteboard.clearContents()
            pasteboard.setString(text, forType: .string)
            pumpClipboard()
        case .paste(let text, let completion):
            clipBusy = true
            if savedClipboard == nil { savedClipboard = snapshot(pasteboard) }
            let item = NSPasteboardItem()
            item.setString(text, forType: .string)
            item.setString("", forType: transientType)
            pasteboard.clearContents()
            pasteboard.writeObjects([item])
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.10) {
                postCmdV()
                completion()
                // 慢 app（Electron/瀏覽器忙碌時）吃貼上要時間；太早換掉剪貼簿 = 貼到別的內容
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.40) {
                    clipBusy = false
                    pumpClipboard()
                }
            }
        }
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
        postKey(CGKeyCode(kVK_ANSI_V), flags: .maskCommand, what: "Cmd-V")
    }

    private static func postKey(_ key: CGKeyCode, flags: CGEventFlags = [], what: String = "按鍵") {
        let source = CGEventSource(stateID: .combinedSessionState)
        guard let keyDown = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true),
              let keyUp = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false) else {
            mdLog("CGEvent 建立失敗（\(what)沒送出）")
            return
        }
        for event in [keyDown, keyUp] {
            event.flags = flags               // 明確指定：使用者正按著 fn 錄下一句也不會帶進去
            event.setIntegerValueField(.eventSourceUserData, value: syntheticMarker)
            event.post(tap: .cgSessionEventTap)
        }
    }

    // MARK: - 撤回／換掉上一句（v0.9）

    enum ReplaceResult {
        case done(String)       // 怎麼換的（log 用）
        case unsafe(String)     // 為什麼沒動
    }

    /// 把游標前剛打進去的 `old` 換成 `new`（new 空字串＝撤回）。main thread；completion 在 main thread。
    ///
    /// 1. AX 讀得到游標前的字：剛好是 old → 用 AX 精準選起來，再貼上（取代選取）或刪掉；不是 old → 不動。
    /// 2. AX 讀不到（Electron、終端機常見）：只有「還在同一個 app、打進去之後沒再打字也沒點滑鼠」
    ///    才按 old 的字數倒退刪除，再貼上 new。倒退鍵在終端機也有效，選取（Shift-←）在終端機無效。
    /// 3. 其他情況不碰使用者的文件，回 .unsafe，由呼叫端把字放剪貼簿＋提示。
    static func replaceLast(_ old: String, with new: String, completion: @escaping (ReplaceResult) -> Void) {
        let tracker = InsertionTracker.shared
        guard PermissionGuide.isAccessibilityTrusted else { completion(.unsafe("輔助使用未授權")); return }
        guard tracker.text == old else { completion(.unsafe("上一句沒有打進任何地方（或已經撤回）")); return }

        let finish: (String) -> Void = { how in
            guard !new.isEmpty else {
                tracker.clear()
                completion(.done(how))
                return
            }
            pasteViaClipboard(new) {
                tracker.record(new)
                completion(.done(how))
            }
        }
        switch axSelectBefore(old) {
        case .selected:
            if new.isEmpty { postKey(CGKeyCode(kVK_Delete), what: "倒退鍵") }   // 刪掉選取；有 new 時 Cmd-V 直接取代選取
            finish("AX 選取")
        case .mismatch:
            completion(.unsafe("游標前的字已經不是上一句"))
        case .unavailable:
            guard tracker.isUntouched else { completion(.unsafe("打進去之後有打字、點滑鼠或換了視窗")); return }
            let n = old.count
            guard n <= 2000 else { completion(.unsafe("上一句太長（\(n) 字），不倒退刪除")); return }
            for _ in 0..<n { postKey(CGKeyCode(kVK_Delete), what: "倒退鍵") }
            finish("倒退 \(n) 字")
        }
    }

    private enum AXSelection { case selected, mismatch, unavailable }

    /// 游標前那段是不是剛好是 text；是就把它選起來。
    private static func axSelectBefore(_ text: String) -> AXSelection {
        let system = AXUIElementCreateSystemWide()
        var focusedObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focusedObj) == .success,
              let focusedRef = focusedObj, CFGetTypeID(focusedRef) == AXUIElementGetTypeID() else { return .unavailable }
        let el = focusedRef as! AXUIElement
        let n = (text as NSString).length                 // AX 的範圍以 UTF-16 計
        guard let caret = selectedRange(el), caret.length == 0, caret.location >= n else { return .unavailable }

        var range = CFRange(location: caret.location - n, length: n)
        guard let rangeValue = AXValueCreate(.cfRange, &range) else { return .unavailable }
        var strObj: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(
                el, kAXStringForRangeParameterizedAttribute as CFString, rangeValue, &strObj) == .success,
              let before = strObj as? String else { return .unavailable }
        guard before == text else { return .mismatch }

        guard AXUIElementSetAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, rangeValue) == .success else {
            return .unavailable
        }
        // 有些 app 回 success 但選取沒變：再讀一次，對不上就把游標放回原處、不動
        guard let now = selectedRange(el), now.location == range.location, now.length == n else {
            var back = caret
            if let backValue = AXValueCreate(.cfRange, &back) {
                _ = AXUIElementSetAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, backValue)
            }
            return .mismatch
        }
        return .selected
    }

    private static func selectedRange(_ el: AXUIElement) -> CFRange? {
        var obj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXSelectedTextRangeAttribute as CFString, &obj) == .success,
              let value = obj, CFGetTypeID(value) == AXValueGetTypeID() else { return nil }
        var range = CFRange()
        return AXValueGetValue(value as! AXValue, .cfRange, &range) ? range : nil
    }
}
