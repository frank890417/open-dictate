import Foundation

/// 從 raw / 校正後文字推「可教的 pair 候選」（啟發式，僅供 UI 建議，不自動入庫）。
enum TeachSuggestions {

    /// 回傳 (wrong, right) 候選，最多 6 組。
    static func pairs(raw: String?, text: String?) -> [(wrong: String, right: String)] {
        guard let raw, let text else { return [] }
        let r = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !r.isEmpty, !t.isEmpty, r != t else { return [] }

        var out: [(String, String)] = []
        var seen = Set<String>()

        func add(_ w: String, _ right: String) {
            let w = w.trimmingCharacters(in: .whitespacesAndNewlines)
            let right = right.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !w.isEmpty, !right.isEmpty, w != right else { return }
            // 整句過長不當 pair
            guard w.count <= 24, right.count <= 24 else { return }
            let key = "\(w)→\(right)"
            guard !seen.contains(key) else { return }
            seen.insert(key)
            out.append((w, right))
        }

        // 1) 若兩邊都很短：整段當一組
        if r.count <= 16, t.count <= 16 {
            add(r, t)
        }

        // 2) 以常見分隔切 token，對齊後比對
        let rawToks = tokens(r)
        let textToks = tokens(t)
        if rawToks.count == textToks.count, rawToks.count >= 2 {
            for i in 0..<rawToks.count {
                if rawToks[i] != textToks[i] {
                    add(rawToks[i], textToks[i])
                }
            }
        }

        // 3) 簡單前後綴對齊找中間差異 span
        if let span = middleDiff(r, t) {
            add(span.0, span.1)
        }

        return Array(out.prefix(6))
    }

    private static func tokens(_ s: String) -> [String] {
        // 空白 / 中英文標點切開
        let pattern = #"[\s,，.。!！?？、；;:：\-—（）()\[\]【】「」『』""'']+"#
        guard let re = try? NSRegularExpression(pattern: pattern) else {
            return s.split { $0.isWhitespace }.map(String.init)
        }
        let range = NSRange(s.startIndex..., in: s)
        let parts = re.stringByReplacingMatches(in: s, range: range, withTemplate: "\u{1e}")
            .split(separator: "\u{1e}")
            .map { String($0) }
            .filter { !$0.isEmpty }
        return parts
    }

    /// 找共同前後綴後的中段差異（只在中段都不太長時）。
    private static func middleDiff(_ a: String, _ b: String) -> (String, String)? {
        let aa = Array(a)
        let bb = Array(b)
        var i = 0
        while i < aa.count, i < bb.count, aa[i] == bb[i] { i += 1 }
        var j = 0
        while j < aa.count - i, j < bb.count - i, aa[aa.count - 1 - j] == bb[bb.count - 1 - j] { j += 1 }
        let aw = String(aa[i..<(aa.count - j)])
        let bw = String(bb[i..<(bb.count - j)])
        guard !aw.isEmpty, !bw.isEmpty, aw != bw, aw.count <= 24, bw.count <= 24 else { return nil }
        return (aw, bw)
    }
}
