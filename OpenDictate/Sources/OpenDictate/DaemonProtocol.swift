import Foundation

// Socket 協議（IO-CONTRACT.md §Socket 協議）：
// unix domain socket /tmp/open-dictate.sock，newline-delimited JSON（一行一則）。

/// 殼 → daemon 的請求。
enum DaemonRequest {
    /// punct："smart_zh" | "llm_zh" | "raw"；fillers："strip" 去掉呃／嗯（daemon v0.8+），nil＝不送（照舊）
    case transcribe(wavPath: String, punct: String, fillers: String? = nil)
    case ping
    case reloadLexicon
    /// 寫入個人詞庫 pair（daemon v0.5+）
    case addPair(wrong: String, right: String, source: String)
    /// 今日統計（daemon v0.5+；殼也可本機讀 log）
    case stats
    /// 辨識模式 "fast" | "accurate"（daemon v0.7+，router 模式；不用重啟 daemon）
    case setMode(String)

    func jsonLine() throws -> Data {
        let dict: [String: Any]
        switch self {
        case .transcribe(let wavPath, let punct, let fillers):
            var d: [String: Any] = ["cmd": "transcribe", "wav": wavPath, "punct": punct]
            if let fillers { d["fillers"] = fillers }
            dict = d
        case .ping:
            dict = ["cmd": "ping"]
        case .reloadLexicon:
            dict = ["cmd": "reload_lexicon"]
        case .addPair(let wrong, let right, let source):
            dict = ["cmd": "add_pair", "wrong": wrong, "right": right, "source": source]
        case .stats:
            dict = ["cmd": "stats"]
        case .setMode(let mode):
            dict = ["cmd": "set_mode", "mode": mode]
        }
        var data = try JSONSerialization.data(withJSONObject: dict, options: [.sortedKeys])
        data.append(0x0A)
        return data
    }
}

/// daemon → 殼 的回應（欄位依 cmd 而異，全部 optional 容錯）。
struct DaemonResponse {
    let ok: Bool
    let text: String?
    let raw: String?
    let changes: [[String]]?
    let punct: String?
    let asrMs: Double?
    let totalMs: Double?
    let pong: Bool
    let model: String?
    let warm: Bool?
    let version: String?
    let replacements: Int?
    /// 辨識模式（daemon v0.7+ router 模式的 ping／set_mode；舊 daemon 為 nil）
    let mode: String?
    /// 目前出字的辨識模型（ping 的 asr.main；set_mode 的 model）
    let asrMain: String?
    let error: String?
    let rawLine: String

    static let errorNoSpeech = "no_speech"

    static func parse(line: Data) throws -> DaemonResponse {
        guard let obj = try? JSONSerialization.jsonObject(with: line),
              let dict = obj as? [String: Any] else {
            throw SocketClientError.badResponse(String(data: line, encoding: .utf8) ?? "<non-utf8>")
        }
        func dbl(_ key: String) -> Double? {
            if let n = dict[key] as? NSNumber { return n.doubleValue }
            return nil
        }
        func int(_ key: String) -> Int? {
            if let n = dict[key] as? NSNumber { return n.intValue }
            return nil
        }
        return DaemonResponse(
            ok: dict["ok"] as? Bool ?? false,
            text: dict["text"] as? String,
            raw: dict["raw"] as? String,
            changes: dict["changes"] as? [[String]],
            punct: dict["punct"] as? String,
            asrMs: dbl("asr_ms"),
            totalMs: dbl("total_ms"),
            pong: dict["pong"] as? Bool ?? false,
            model: dict["model"] as? String,
            warm: dict["warm"] as? Bool,
            version: dict["version"] as? String,
            replacements: int("replacements"),
            mode: dict["mode"] as? String,
            asrMain: (dict["asr"] as? [String: Any])?["main"] as? String ?? (dict["mode"] != nil ? dict["model"] as? String : nil),
            error: dict["error"] as? String,
            rawLine: String(data: line, encoding: .utf8) ?? ""
        )
    }
}
