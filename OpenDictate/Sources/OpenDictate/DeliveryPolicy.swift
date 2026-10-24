import Foundation

/// 一句的結果回來時，要直接打進文件、還是改放剪貼簿（v0.9 連講＋逾時不丟）。純邏輯，抽出來好測。
///
/// - 被後面那句超車（後面那句已經打出去）→ 不插隊，放剪貼簿。
/// - 晚到（過了殼的逾時、或從結果檔撿回來）時再看兩件事：焦點換了 app、放開熱鍵後使用者已經接著打字 → 放剪貼簿。
/// - 準時回來的照舊打在目前的游標（跟 v0.8 一樣，不看焦點）。
enum DeliveryPolicy {
    /// nil＝打字；否則回不打字的原因（給 log 與提示）
    static func holdBack(jobID: Int, lastDeliveredJobID: Int, late: Bool,
                         targetPID: pid_t?, frontPID: pid_t?,
                         releasedAt: Date, lastUserInputAt: Date?) -> String? {
        if jobID < lastDeliveredJobID { return "後面那句已經先打出去了" }
        guard late else { return nil }
        if let targetPID, frontPID != targetPID { return "已經換到別的視窗" }
        if let typed = lastUserInputAt, typed > releasedAt { return "放開之後你已經接著打字了" }
        return nil
    }
}
