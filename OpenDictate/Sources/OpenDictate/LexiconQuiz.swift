import Foundation

/// 每日詞庫題（「今天 3 題」）：外部腳本出題、記答案，殼只負責顯示與轉送是／否。
///
/// 腳本放在詞庫根目錄 `tools/muse-dictate/lexicon-quiz.py`（跟 CLI 寫詞庫的退路同一個根，見
/// AppDelegate.addLexiconPairViaCLI）；沒有這支腳本的產品（公開版預設）卡片就不出現。
///   today --json [--peek]                         → 今天的題目＋佇列剩幾題＋排程健康
///   answer <id> yes|no --via control-center --json → 回答一題（是＝寫進詞庫並熱重載，約 0.3–1 秒）
/// 為什麼要有：一個月「回報誤聽」按了 0 次——不會有人去按，但每天三題是／否會被答（2026-10）。
enum LexiconQuiz {

    struct Question: Identifiable, Equatable {
        let id: String
        let wrong: String
        let right: String
        let question: String
        let sample: String
        var status: String          // pending / yes / no

        var isPending: Bool { status == "pending" }
    }

    struct Snapshot: Equatable {
        var questions: [Question]
        let pendingTotal: Int
        /// 排程太久沒跑（health.*.stale）：「聽寫渦流 40 小時沒跑」
        let staleNotes: [String]

        var pendingToday: Int { questions.filter(\.isPending).count }
    }

    static var scriptPath: String { "\(ProductConfig.lexiconRoot)/tools/muse-dictate/lexicon-quiz.py" }
    static var isAvailable: Bool { FileManager.default.fileExists(atPath: scriptPath) }

    private static var python: String {
        let venv = "\(ProductConfig.lexiconRoot)/tools/td-subtitle/.venv/bin/python3"
        return FileManager.default.isExecutableFile(atPath: venv) ? venv : "/usr/bin/python3"
    }

    /// 背景執行緒呼叫。peek＝只看不記（選單列圖示的小點用）；開控制中心用 peek=false（記下今天問了哪三題）。
    static func today(peek: Bool) -> Snapshot? {
        guard isAvailable,
              let obj = run(["today", "--json"] + (peek ? ["--peek"] : [])) else { return nil }
        let qs = (obj["questions"] as? [[String: Any]] ?? []).compactMap { q -> Question? in
            guard let id = q["id"] as? String else { return nil }
            return Question(id: id,
                            wrong: q["wrong"] as? String ?? "",
                            right: q["right"] as? String ?? "",
                            question: q["question"] as? String ?? "",
                            sample: q["sample"] as? String ?? "",
                            status: q["status"] as? String ?? "pending")
        }
        var stale: [String] = []
        for (name, v) in (obj["health"] as? [String: Any] ?? [:]).sorted(by: { $0.key < $1.key }) {
            guard let h = v as? [String: Any], h["stale"] as? Bool == true else { continue }
            let label = name == "dictate-vortex" ? "聽寫渦流" : (name == "lexicon-grow" ? "詞庫候選" : name)
            if let hours = (h["hours"] as? NSNumber)?.doubleValue {
                stale.append("\(label) \(Int(hours)) 小時沒跑")
            } else {
                stale.append("\(label) 沒有執行紀錄")
            }
        }
        return Snapshot(questions: qs, pendingTotal: (obj["pending_total"] as? NSNumber)?.intValue ?? 0, staleNotes: stale)
    }

    /// 背景執行緒呼叫。回傳這題的新狀態（yes／no）；nil＝失敗（呼叫端重讀 today）。
    static func answer(id: String, yes: Bool) -> String? {
        guard let obj = run(["answer", id, yes ? "yes" : "no", "--via", "control-center", "--json"]),
              obj["ok"] as? Bool == true else { return nil }
        return obj["status"] as? String ?? (yes ? "yes" : "no")
    }

    private static func run(_ args: [String]) -> [String: Any]? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [scriptPath] + args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
        } catch {
            mdLog("詞庫題腳本跑不起來：\(error)")
            return nil
        }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            mdLog("詞庫題腳本失敗（\(args.first ?? "?")，exit \(p.terminationStatus)）")
            return nil
        }
        return obj
    }
}
