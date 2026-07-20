import AppKit

// headless probe 模式（socket / wav 管線驗證），沒命中才走 GUI
if let code = ProbeCLI.run(arguments: CommandLine.arguments) {
    exit(code)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
// menubar-only：不進 Dock、不搶焦點（bundle 內另有 LSUIElement=true，裸跑時靠這行）
app.setActivationPolicy(.accessory)
app.run()
