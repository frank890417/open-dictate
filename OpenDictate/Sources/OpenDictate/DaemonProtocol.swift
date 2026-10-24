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
    /// 剛才那句（transcribe 回的 uid）用另一個模式再辨識一次（daemon v0.9+，router 模式；錄音只留 recent_audio_s 秒）
    case retranscribe(uid: String, mode: String = "accurate", punct: String, fillers: String? = nil)

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
        case .retranscribe(let uid, let mode, let punct, let fillers):
            var d: [String: Any] = ["cmd": "retranscribe", "uid": uid, "mode": mode, "punct": punct]
            if let fillers { d["fillers"] = fillers }
            dict = d
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
    /// 這句的 id（daemon v0.9+）：重轉時用
    let uid: String?
    /// retranscribe 的回應：重轉的是哪一句
    let redoOf: String?
    /// ping（daemon v0.9+）：錄音留幾秒可以重轉；nil＝這個 daemon 不能重轉
    let recentAudioSeconds: Double?
    /// ping（daemon v0.9+）：最後結果檔的位置
    let resultsPath: String?
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
            uid: dict["uid"] as? String,
            redoOf: dict["redo_of"] as? String,
            recentAudioSeconds: dbl("recent_audio_s"),
            resultsPath: dict["results_path"] as? String,
            rawLine: String(data: line, encoding: .utf8) ?? ""
        )
    }
}

/// daemon 的最後結果檔（v0.9+，IO-CONTRACT §Daemon 0.9.0）：socket 等不到回應時用 wav 路徑找回那一句。
enum DaemonResults {
    /// ping 回報的位置優先；還沒 ping 過就用產品設定的資料夾
    static var path: String = "\(ProductConfig.dataRoot)/dictation-last.json"

    static func find(wavPath: String) -> DaemonResponse? {
        guard let data = FileManager.default.contents(atPath: path),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let rows = obj["results"] as? [[String: Any]],
              let row = rows.first(where: { ($0["wav"] as? String) == wavPath }),
              let line = try? JSONSerialization.data(withJSONObject: row) else { return nil }
        return try? DaemonResponse.parse(line: line)
    }
}
