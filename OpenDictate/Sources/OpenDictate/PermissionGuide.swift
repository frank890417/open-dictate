import AppKit
import ApplicationServices
import AVFoundation

/// 首次啟動權限檢查與引導。
/// 全套授權 = 麥克風 + 輔助使用（fn 監聽 / Cmd-V 注入）+ 輸入監控（部分系統版本對全域鍵盤監聽要求）。
enum PermissionGuide {

    // 系統設定 deep link
    private static let accessibilityPane = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    private static let inputMonitoringPane = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    private static let microphonePane = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"

    static var isAccessibilityTrusted: Bool {
        AXIsProcessTrusted()
    }

    /// 啟動流程：先要麥克風（觸發系統原生詢問），再驗 Accessibility（未授權 → 系統 prompt + 引導視窗）。
    static func runStartupChecks() {
        requestMicrophone()
        if !isAccessibilityTrusted {
            // 讓系統跳原生「要求輔助使用」對話框（把 app 加進清單，等使用者打勾）
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            showGuideAlert()
        }
    }

    private static func requestMicrophone() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            break
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                mdLog("麥克風權限：\(granted ? "granted" : "denied")")
            }
        case .denied, .restricted:
            DispatchQueue.main.async {
                Notifier.notify(title: "\(ProductConfig.appName) 沒有麥克風權限",
                                body: "系統設定 > 隱私權與安全性 > 麥克風，勾選 \(ProductConfig.appName)")
                NSWorkspace.shared.open(URL(string: microphonePane)!)
            }
        @unknown default:
            break
        }
    }

    /// 引導視窗（NSAlert）：解釋三項權限 + 即時 checklist + 按鈕直開對應設定面板。
    static func showGuideAlert() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "\(ProductConfig.appName) 需要授權才能運作"
        if let img = UITheme.symbolImage(UITheme.Symbol.permissions, pointSize: 32) {
            alert.icon = img
        }

        let micOK: Bool = {
            switch AVCaptureDevice.authorizationStatus(for: .audio) {
            case .authorized: return true
            default: return false
            }
        }()
        let axOK = isAccessibilityTrusted
        // Input Monitoring 無法可靠查詢；提示使用者自行確認
        let checklist = """
        即時狀態：
        \(axOK ? "✓" : "○") 輔助使用（Accessibility）— fn 監聽 + Cmd-V 注入
        ○ 輸入監控（Input Monitoring）— 全域鍵盤（請在設定確認）
        \(micOK ? "✓" : "○") 麥克風 — 錄音

        勾選後請結束並重開 \(ProductConfig.appName)。
        完整步驟見 docs/SETUP.md。
        """
        alert.informativeText = checklist
        alert.addButton(withTitle: "打開「輔助使用」設定")
        alert.addButton(withTitle: "打開「輸入監控」設定")
        alert.addButton(withTitle: "打開「麥克風」設定")
        alert.addButton(withTitle: "稍後")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            NSWorkspace.shared.open(URL(string: accessibilityPane)!)
        case .alertSecondButtonReturn:
            NSWorkspace.shared.open(URL(string: inputMonitoringPane)!)
        case .alertThirdButtonReturn:
            NSWorkspace.shared.open(URL(string: microphonePane)!)
        default:
            break
        }
    }
}
