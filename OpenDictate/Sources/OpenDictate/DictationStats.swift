import Foundation

/// 讀取 ~/.open-dictate/dictation-log/YYYY-MM-DD.jsonl 的今日統計（殼端，不經 daemon 也可）。
struct DictationStats {
    let count: Int
    let okCount: Int
    let errorCount: Int
    let lexiconHits: Int
    let p50Ms: Int?
    let p90Ms: Int?
    let maxMs: Int?

    static var logDir: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".muse/dictation-log")
    }

    static func todayFile() -> URL {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return logDir.appendingPathComponent("\(f.string(from: Date())).jsonl")
    }

    static func loadToday() -> DictationStats {
        load(from: todayFile())
    }

    static func load(from url: URL) -> DictationStats {
        guard let data = try? String(contentsOf: url, encoding: .utf8) else {
            return DictationStats(count: 0, okCount: 0, errorCount: 0, lexiconHits: 0,
                                  p50Ms: nil, p90Ms: nil, maxMs: nil)
        }
        var ok = 0, err = 0, hits = 0
        var latencies: [Int] = []
        for line in data.split(separator: "\n") {
            guard let row = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { continue }
            if let e = row["error"] as? String, !e.isEmpty {
                err += 1
                continue
            }
            let text = (row["text"] as? String) ?? ""
            if text.isEmpty && (row["ok"] as? Bool) == false {
                err += 1
                continue
            }
            if !text.isEmpty || row["total_ms"] != nil {
                ok += 1
            }
            if let ch = row["changes"] as? [Any], !ch.isEmpty { hits += 1 }
            if let ms = row["total_ms"] as? NSNumber {
                latencies.append(ms.intValue)
            } else if let ms = row["total_ms"] as? Int {
                latencies.append(ms)
            } else if let ms = row["total_ms"] as? Double {
                latencies.append(Int(ms))
            }
        }
        latencies.sort()
        func pct(_ p: Double) -> Int? {
            guard !latencies.isEmpty else { return nil }
            let i = min(latencies.count - 1, Int(Double(latencies.count - 1) * p))
            return latencies[i]
        }
        return DictationStats(
            count: ok + err,
            okCount: ok,
            errorCount: err,
            lexiconHits: hits,
            p50Ms: pct(0.5),
            p90Ms: pct(0.9),
            maxMs: latencies.last
        )
    }

    var summaryLine: String {
        if count == 0 { return "今日尚無聽寫" }
        var parts = ["今日 \(okCount) 句"]
        if let p50 = p50Ms { parts.append("p50 \(p50)ms") }
        if lexiconHits > 0 { parts.append("詞庫命中 \(lexiconHits)") }
        if errorCount > 0 { parts.append("失敗 \(errorCount)") }
        return parts.joined(separator: " · ")
    }

    var detailBlock: String {
        if count == 0 { return "今日尚無聽寫記錄。" }
        var lines = [
            "成功 \(okCount) 句",
            "失敗 / no_speech \(errorCount)",
            "詞庫有改動 \(lexiconHits) 句",
        ]
        if let p50 = p50Ms { lines.append("延遲 p50 \(p50)ms") }
        if let p90 = p90Ms { lines.append("延遲 p90 \(p90)ms") }
        if let mx = maxMs { lines.append("延遲 max \(mx)ms") }
        return lines.joined(separator: "\n")
    }

    /// 一筆成功聽寫記錄（供殼端 timeout 復原用，見 lastSuccessEntry）。
    struct Entry {
        let ts: String?
        let text: String
        let raw: String?
        let changes: [[String]]
        let totalMs: Int?
    }

    /// 讀 log 檔最後一筆「成功」記錄（text 非空、無 error）。
    ///
    /// 殼的 lastText 只在 socket 收到回應時更新；長口述 + llm_zh fallback 可能讓 daemon
    /// 處理時間超過殼的逾時門檻，殼判定 timeout 但 daemon 其實跑完並寫了 log（2026-07-16 事故）。
    /// 開選單時核對這筆，讓「複製上一句」拿得到那句話，不必只信殼的記憶體狀態。
    static func lastSuccessEntry(from url: URL = DictationStats.todayFile()) -> Entry? {
        guard let data = try? String(contentsOf: url, encoding: .utf8) else { return nil }
        for line in data.split(separator: "\n").reversed() {
            guard let row = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { continue }
            if let e = row["error"] as? String, !e.isEmpty { continue }
            guard let text = row["text"] as? String, !text.isEmpty else { continue }
            let totalMs: Int?
            if let n = row["total_ms"] as? NSNumber { totalMs = n.intValue } else { totalMs = nil }
            return Entry(ts: row["ts"] as? String, text: text, raw: row["raw"] as? String,
                        changes: (row["changes"] as? [[String]]) ?? [], totalMs: totalMs)
        }
        return nil
    }
}
