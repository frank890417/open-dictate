import AppKit
import ObjectiveC

/// 背景時不設滑鼠指標。
///
/// 選單列圖示的視窗（NSStatusBarWindow）在背景也會收到滑鼠移動，AppKit 的追蹤區域每次都會把指標設回箭頭。
/// 平常 WindowServer 不理背景 app 設的指標；但系統開了「自訂指標顏色」時，設指標改走輔助使用的全域路徑，
/// 箭頭會蓋過前景 app 的 I 字／手指，滑鼠停在輸入框、按鈕上就來回閃（2026-10-23 實撞）。
/// 我們自己的視窗（設定、聽錯面板）都會先 activate，前景時照常設。
enum CursorGuard {
    static func install() {
        guard let original = class_getInstanceMethod(NSCursor.self, #selector(NSCursor.set)),
              let guarded = class_getInstanceMethod(NSCursor.self, #selector(NSCursor.od_setIfActive))
        else { return }
        method_exchangeImplementations(original, guarded)
    }
}

fileprivate extension NSCursor {
    @objc func od_setIfActive() {
        guard NSApp?.isActive == true else { return }
        od_setIfActive()  // 已交換實作：這裡跑的是原本的 set
    }
}
