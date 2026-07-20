import AppKit
import ObjectiveC

/// 「回報誤聽」面板：可預填、可顯示多組建議 pair。
enum MishearPanel {

    /// - Parameters:
    ///   - seedWrong / seedRight: 預填欄位
    ///   - contextHint: 說明文字
    ///   - suggestions: 可點選的候選（點了填入兩欄）
    ///   - completion: (wrong, right)
    static func present(seedWrong: String? = nil,
                        seedRight: String? = nil,
                        contextHint: String? = nil,
                        suggestions: [(wrong: String, right: String)] = [],
                        completion: @escaping (String, String) -> Void) {
        let alert = NSAlert()
        alert.messageText = "教詞庫：聽錯了什麼？"
        var info = "只填「聽錯的詞 → 正確的詞」（例：台灣點 → OpenDictate）。整句不用貼，詞庫是逐詞替換。"
        if let ctx = contextHint, !ctx.isEmpty {
            info += "\n\n\(String(ctx.prefix(200)))\(ctx.count > 200 ? "…" : "")"
        }
        alert.informativeText = info
        alert.addButton(withTitle: "學起來")
        alert.addButton(withTitle: "取消")
        if let img = UITheme.symbolImage(UITheme.Symbol.mishear, pointSize: 28) {
            alert.icon = img
        }

        let height: CGFloat = suggestions.isEmpty ? 72 : min(72 + CGFloat(suggestions.count) * 26 + 8, 200)
        let stack = NSStackView(frame: NSRect(x: 0, y: 0, width: 360, height: height))
        stack.orientation = .vertical
        stack.spacing = 6
        stack.alignment = .leading

        let wrongField = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 28))
        wrongField.placeholderString = "聽錯的詞（例：開放聽寫）"
        wrongField.stringValue = seedWrong ?? ""
        wrongField.font = .systemFont(ofSize: 13)

        let rightField = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 28))
        rightField.placeholderString = "正確的詞（例：OpenDictate）"
        rightField.stringValue = seedRight ?? ""
        rightField.font = .systemFont(ofSize: 13)

        stack.addArrangedSubview(wrongField)
        stack.addArrangedSubview(rightField)

        if !suggestions.isEmpty {
            let lab = NSTextField(labelWithString: "建議（點一下填入）：")
            lab.font = .systemFont(ofSize: 11)
            lab.textColor = .secondaryLabelColor
            stack.addArrangedSubview(lab)
            for (idx, pair) in suggestions.prefix(5).enumerated() {
                let btn = NSButton(
                    title: "\(pair.wrong) → \(pair.right)",
                    target: nil,
                    action: nil
                )
                btn.bezelStyle = .inline
                btn.font = .systemFont(ofSize: 11)
                btn.tag = idx
                // 用 closure 橋：target/action 需 object — 改為在 present 後用 representedObject 不方便；
                // 這裡用简单的 target wrapper
                let binder = SuggestionBinder(wrong: pair.wrong, right: pair.right, wrongField: wrongField, rightField: rightField)
                btn.target = binder
                btn.action = #selector(SuggestionBinder.apply)
                // 保活 binder
                objc_setAssociatedObject(btn, &AssociatedKeys.binder, binder, .OBJC_ASSOCIATION_RETAIN)
                stack.addArrangedSubview(btn)
            }
        }

        alert.accessoryView = stack
        alert.window.initialFirstResponder = wrongField.stringValue.isEmpty ? wrongField : rightField

        NSApp.activate(ignoringOtherApps: true)
        if alert.runModal() == .alertFirstButtonReturn {
            let wrong = wrongField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let right = rightField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !wrong.isEmpty, !right.isEmpty, wrong != right else { return }
            completion(wrong, right)
        }
    }

    /// 從選取文字 / 剪貼簿啟動。
    static func presentFromSelection(completion: @escaping (String, String) -> Void) {
        let sel = SelectionReader.selectedText()
        let clip = SelectionReader.clipboardText()
        let seed = sel ?? clip
        var hint = "流程：在文件裡選取「聽錯的詞」→ 開此面板 → 填正確詞。"
        if sel != nil {
            hint += "\n來源：目前選取"
        } else if clip != nil {
            hint += "\n來源：剪貼簿（未偵測到選取）"
        } else {
            hint += "\n未偵測到選取或剪貼簿內容，請手動填。"
        }
        present(seedWrong: seed, seedRight: nil, contextHint: hint, completion: completion)
    }
}

private enum AssociatedKeys {
    static var binder: UInt8 = 0
}

private final class SuggestionBinder: NSObject {
    let wrong: String
    let right: String
    weak var wrongField: NSTextField?
    weak var rightField: NSTextField?

    init(wrong: String, right: String, wrongField: NSTextField, rightField: NSTextField) {
        self.wrong = wrong
        self.right = right
        self.wrongField = wrongField
        self.rightField = rightField
    }

    @objc func apply() {
        wrongField?.stringValue = wrong
        rightField?.stringValue = right
    }
}
