import AppKit

/// OpenDictate 視覺語言：錄音室儀表，不是聊天機器人。
/// 鐵律：所有 UI 不搶焦點（nonactivating / ignoresMouseEvents / accessory）。
enum UITheme {
    // MARK: - Color (semantic)

    static let record = NSColor.systemRed
    static let transcribe = NSColor.systemOrange
    static let success = NSColor.systemGreen
    static let warning = NSColor.systemYellow
    static let error = NSColor.systemOrange
    static let muted = NSColor.secondaryLabelColor
    static let primary = NSColor.labelColor

    // MARK: - Layout

    static let hudCorner: CGFloat = 12
    static let hudPadX: CGFloat = 12
    static let hudPadY: CGFloat = 8
    static let menuSymbolPointSize: CGFloat = 13
    static let statusSymbolPointSize: CGFloat = 14

    // MARK: - Timing

    static let successHold: TimeInterval = 1.35
    static let errorHold: TimeInterval = 2.0
    static let hudFade: TimeInterval = 0.18

    // MARK: - SF Symbols

    enum Symbol {
        static let idle = "mic.fill"
        static let recording = "mic.fill"
        static let transcribing = "waveform"
        static let success = "checkmark.circle.fill"
        static let error = "exclamationmark.triangle.fill"
        static let offline = "antenna.radiowaves.left.and.right.slash"
        static let settings = "gearshape"
        static let history = "clock.arrow.circlepath"
        static let lexicon = "text.book.closed"
        static let mishear = "arrow.left.arrow.right"
        static let log = "doc.text"
        static let permissions = "lock.shield"
        static let restart = "arrow.clockwise"
        static let quit = "power"
        static let copy = "doc.on.doc"
    }

    static func symbolImage(_ name: String, pointSize: CGFloat = statusSymbolPointSize, weight: NSFont.Weight = .medium) -> NSImage? {
        let conf = NSImage.SymbolConfiguration(pointSize: pointSize, weight: weight)
        guard let img = NSImage(systemSymbolName: name, accessibilityDescription: nil) else { return nil }
        let configured = img.withSymbolConfiguration(conf) ?? img
        configured.isTemplate = true
        return configured
    }

    static func menuSymbol(_ name: String) -> NSImage? {
        symbolImage(name, pointSize: menuSymbolPointSize, weight: .regular)
    }
}
