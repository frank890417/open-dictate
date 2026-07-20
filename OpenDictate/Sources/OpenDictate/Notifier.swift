import Foundation
import AppKit
import UserNotifications

/// 使用者通知（UNUserNotificationCenter，NSUserNotification 的現代替代）。
///
/// 重要：UNUserNotificationCenter 只能在「真的 .app bundle」裡用——
/// 用 `swift run` 跑裸 executable 時呼叫會直接 ObjC exception crash（bundleProxy 為 nil）。
/// 所以先驗 bundle 再用；裸跑時降級成 NSLog。
enum Notifier {
    static var isAvailable: Bool {
        Bundle.main.bundleIdentifier != nil && Bundle.main.bundlePath.hasSuffix(".app")
    }

    static func setup() {
        guard isAvailable else {
            mdLog("非 .app bundle 執行——通知降級為 log")
            return
        }
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { granted, error in
            if let error { mdLog("通知授權失敗：\(error.localizedDescription)") }
            else { mdLog("通知授權：\(granted ? "granted" : "denied")") }
        }
    }

    static func notify(title: String, body: String) {
        mdLog("通知：\(title) — \(body)")
        guard isAvailable else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil // 立即
        )
        UNUserNotificationCenter.current().add(request) { error in
            if let error { mdLog("通知送出失敗：\(error.localizedDescription)") }
        }
    }
}
