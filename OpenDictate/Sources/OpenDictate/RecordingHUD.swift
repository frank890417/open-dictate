import AppKit
import ApplicationServices
import QuartzCore

/// 錄音浮動提示（游標旁；拿不到游標 → 螢幕底部中央）。
/// recording / transcribing / success（可含 raw 對照）/ error。
/// nonactivating + ignoresMouseEvents：絕不搶焦點。
final class RecordingHUD {

    enum HUDState {
        case recording
        case transcribing
        case success(text: String, raw: String?, latencyMs: Int?, changes: [[String]]?)
        case error(message: String)
    }

    private let panel: NSPanel
    private let effect: NSVisualEffectView
    private let borderLayer = CALayer()
    private let waveView = WaveView()
    private let iconView = NSImageView()
    private let timeLabel = NSTextField(labelWithString: "0.0s")
    private let phaseLabel = NSTextField(labelWithString: "")
    private let previewLabel = NSTextField(labelWithString: "")
    private let diffLabel = NSTextField(labelWithString: "")
    private var timer: Timer?
    private var startedAt: Date?
    private var hideWorkItem: DispatchWorkItem?
    private var pulseTimer: Timer?
    private var recordingActive = false

    private static let compactSize = NSSize(width: 208, height: 44)
    private static let expandedSize = NSSize(width: 300, height: 72)
    private static let expandedDiffSize = NSSize(width: 300, height: 88)

    init() {
        panel = NSPanel(
            contentRect: NSRect(origin: .zero, size: Self.compactSize),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered, defer: true
        )
        panel.isFloatingPanel = true
        panel.level = .statusBar
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.alphaValue = 0

        effect = NSVisualEffectView(frame: NSRect(origin: .zero, size: Self.compactSize))
        effect.material = .hudWindow
        effect.state = .active
        effect.blendingMode = .behindWindow
        effect.wantsLayer = true
        effect.layer?.cornerRadius = UITheme.hudCorner
        effect.layer?.masksToBounds = true

        borderLayer.borderWidth = 1
        borderLayer.borderColor = NSColor.white.withAlphaComponent(0.12).cgColor
        borderLayer.cornerRadius = UITheme.hudCorner
        borderLayer.frame = effect.bounds
        borderLayer.autoresizingMask = [.layerWidthSizable, .layerHeightSizable]
        effect.layer?.addSublayer(borderLayer)

        iconView.frame = NSRect(x: 12, y: 11, width: 22, height: 22)
        iconView.imageScaling = .scaleProportionallyUpOrDown
        iconView.contentTintColor = UITheme.record

        waveView.frame = NSRect(x: 40, y: 8, width: 112, height: 28)

        timeLabel.frame = NSRect(x: 156, y: 13, width: 44, height: 18)
        timeLabel.font = .monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        timeLabel.textColor = UITheme.muted
        timeLabel.alignment = .right

        phaseLabel.frame = NSRect(x: 40, y: 13, width: 156, height: 18)
        phaseLabel.font = .systemFont(ofSize: 12, weight: .medium)
        phaseLabel.textColor = UITheme.muted
        phaseLabel.isHidden = true

        previewLabel.frame = NSRect(x: 40, y: 10, width: 248, height: 16)
        previewLabel.font = .systemFont(ofSize: 11, weight: .regular)
        previewLabel.textColor = UITheme.muted
        previewLabel.lineBreakMode = .byTruncatingTail
        previewLabel.isHidden = true
        previewLabel.maximumNumberOfLines = 1

        diffLabel.frame = NSRect(x: 40, y: 8, width: 248, height: 14)
        diffLabel.font = .systemFont(ofSize: 10, weight: .regular)
        diffLabel.textColor = NSColor.systemYellow
        diffLabel.lineBreakMode = .byTruncatingTail
        diffLabel.isHidden = true
        diffLabel.maximumNumberOfLines = 1

        effect.addSubview(iconView)
        effect.addSubview(waveView)
        effect.addSubview(timeLabel)
        effect.addSubview(phaseLabel)
        effect.addSubview(previewLabel)
        effect.addSubview(diffLabel)
        panel.contentView = effect
    }

    func show(_ state: HUDState) {
        hideWorkItem?.cancel()
        hideWorkItem = nil
        stopPulse()

        switch state {
        case .recording:
            recordingActive = true
            resize(to: Self.compactSize, layout: .compact)
            startedAt = Date()
            silentStreak = 0
            setIcon(UITheme.Symbol.recording, tint: UITheme.record)
            waveView.isHidden = false
            waveView.barColor = UITheme.record
            timeLabel.isHidden = false
            phaseLabel.isHidden = true
            previewLabel.isHidden = true
            diffLabel.isHidden = true
            waveView.reset()
            setBorder(UITheme.record.withAlphaComponent(0.35))
            startTimer()
            startPulse()

        case .transcribing:
            recordingActive = false
            resize(to: Self.compactSize, layout: .compact)
            setIcon(UITheme.Symbol.transcribing, tint: UITheme.transcribe)
            waveView.isHidden = true
            timeLabel.isHidden = true
            phaseLabel.stringValue = "轉錄中…"
            phaseLabel.textColor = UITheme.transcribe
            phaseLabel.isHidden = false
            previewLabel.isHidden = true
            diffLabel.isHidden = true
            setBorder(UITheme.transcribe.withAlphaComponent(0.35))
            stopTimer()

        case .success(let text, let raw, let latencyMs, let changes):
            recordingActive = false
            let changePairs = changes ?? []
            let showDiff = DictateSettings.showRawDiff
                && raw != nil
                && raw != text
                && !(raw ?? "").isEmpty
            let size = showDiff || !changePairs.isEmpty ? Self.expandedDiffSize : Self.expandedSize
            resize(to: size, layout: .expanded(hasDiff: showDiff || !changePairs.isEmpty))
            setIcon(UITheme.Symbol.success, tint: UITheme.success)
            waveView.isHidden = true
            timeLabel.isHidden = true
            let ms = latencyMs.map { " · \($0)ms" } ?? ""
            let hit = changePairs.isEmpty ? "" : " · 詞庫 \(changePairs.count)"
            phaseLabel.stringValue = "已插入\(ms)\(hit)"
            phaseLabel.textColor = UITheme.success
            phaseLabel.isHidden = false
            previewLabel.stringValue = text.replacingOccurrences(of: "\n", with: " ")
            previewLabel.isHidden = text.isEmpty
            if showDiff {
                diffLabel.stringValue = "raw：\((raw ?? "").replacingOccurrences(of: "\n", with: " "))"
                diffLabel.isHidden = false
            } else if !changePairs.isEmpty {
                let bits = changePairs.prefix(3).map { p in
                    let w = p.first ?? "?"
                    let r = p.count > 1 ? p[1] : "?"
                    return "\(w)→\(r)"
                }
                diffLabel.stringValue = bits.joined(separator: " · ")
                diffLabel.isHidden = false
            } else {
                diffLabel.isHidden = true
            }
            setBorder(UITheme.success.withAlphaComponent(0.4))
            stopTimer()
            scheduleHide(after: UITheme.successHold)

        case .error(let message):
            recordingActive = false
            resize(to: Self.expandedSize, layout: .expanded(hasDiff: false))
            setIcon(UITheme.Symbol.error, tint: UITheme.error)
            waveView.isHidden = true
            timeLabel.isHidden = true
            phaseLabel.stringValue = message
            phaseLabel.textColor = UITheme.error
            phaseLabel.isHidden = false
            previewLabel.isHidden = true
            diffLabel.isHidden = true
            setBorder(UITheme.error.withAlphaComponent(0.4))
            stopTimer()
            scheduleHide(after: UITheme.errorHold)
        }

        position()
        fadeIn()
    }

    func hide() {
        hideWorkItem?.cancel()
        hideWorkItem = nil
        stopTimer()
        stopPulse()
        fadeOut()
    }

    private var silentStreak = 0
    func updateLevel(_ level: Float) {
        waveView.push(level)
        if level < 0.03 {
            silentStreak += 1
            if silentStreak == 30 {
                phaseLabel.stringValue = "沒收到聲音…麥克風對嗎？"
                phaseLabel.textColor = UITheme.warning
                phaseLabel.isHidden = false
                timeLabel.isHidden = true
                setBorder(UITheme.warning.withAlphaComponent(0.45))
            }
        } else {
            silentStreak = 0
            if recordingActive, !phaseLabel.isHidden {
                phaseLabel.isHidden = true
                timeLabel.isHidden = false
                setBorder(UITheme.record.withAlphaComponent(0.35))
            }
        }
    }

    private enum Layout { case compact, expanded(hasDiff: Bool) }

    private func resize(to size: NSSize, layout: Layout) {
        var frame = panel.frame
        frame.size = size
        panel.setFrame(frame, display: false)
        effect.frame = NSRect(origin: .zero, size: size)
        borderLayer.frame = effect.bounds

        switch layout {
        case .compact:
            iconView.frame = NSRect(x: 12, y: 11, width: 22, height: 22)
            waveView.frame = NSRect(x: 40, y: 8, width: size.width - 96, height: 28)
            timeLabel.frame = NSRect(x: size.width - 56, y: 13, width: 44, height: 18)
            phaseLabel.frame = NSRect(x: 40, y: 13, width: size.width - 56, height: 18)
            previewLabel.isHidden = true
            diffLabel.isHidden = true
        case .expanded(let hasDiff):
            iconView.frame = NSRect(x: 12, y: hasDiff ? 54 : 38, width: 22, height: 22)
            phaseLabel.frame = NSRect(x: 40, y: hasDiff ? 56 : 40, width: size.width - 56, height: 18)
            previewLabel.frame = NSRect(x: 40, y: hasDiff ? 28 : 12, width: size.width - 52, height: 16)
            diffLabel.frame = NSRect(x: 40, y: 10, width: size.width - 52, height: 14)
        }
    }

    private func setIcon(_ name: String, tint: NSColor) {
        iconView.image = UITheme.symbolImage(name, pointSize: 16, weight: .semibold)
        iconView.contentTintColor = tint
    }

    private func setBorder(_ color: NSColor) {
        borderLayer.borderColor = color.cgColor
    }

    private func fadeIn() {
        panel.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = UITheme.hudFade
            ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().alphaValue = 1
        }
    }

    private func fadeOut() {
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = UITheme.hudFade
            ctx.timingFunction = CAMediaTimingFunction(name: .easeIn)
            panel.animator().alphaValue = 0
        }, completionHandler: { [weak self] in
            self?.panel.orderOut(nil)
        })
    }

    private func scheduleHide(after delay: TimeInterval) {
        hideWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.hide() }
        hideWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func position() {
        if let caret = Self.caretScreenRect() {
            var origin = NSPoint(x: caret.midX - panel.frame.width / 2, y: caret.maxY + 8)
            origin = clampToScreen(origin, near: caret)
            panel.setFrameOrigin(origin)
        } else {
            let screen = NSScreen.main ?? NSScreen.screens[0]
            let f = screen.visibleFrame
            panel.setFrameOrigin(NSPoint(x: f.midX - panel.frame.width / 2, y: f.minY + 96))
        }
    }

    private func clampToScreen(_ origin: NSPoint, near rect: NSRect) -> NSPoint {
        let screen = NSScreen.screens.first { $0.frame.intersects(rect) } ?? NSScreen.main ?? NSScreen.screens[0]
        var p = origin
        let f = screen.visibleFrame
        let w = panel.frame.width
        let h = panel.frame.height
        p.x = max(f.minX + 8, min(p.x, f.maxX - w - 8))
        if p.y + h > f.maxY - 8 { p.y = rect.minY - h - 8 }
        p.y = max(f.minY + 8, min(p.y, f.maxY - h - 8))
        return p
    }

    private static func caretScreenRect() -> NSRect? {
        let system = AXUIElementCreateSystemWide()
        var focusedObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focusedObj) == .success,
              let focusedRef = focusedObj, CFGetTypeID(focusedRef) == AXUIElementGetTypeID() else { return nil }
        let element = focusedRef as! AXUIElement

        var rangeObj: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, &rangeObj) == .success,
              let rangeRef = rangeObj, CFGetTypeID(rangeRef) == AXValueGetTypeID() else { return nil }
        var range = CFRange()
        guard AXValueGetValue(rangeRef as! AXValue, .cfRange, &range) else { return nil }

        var rangeCopy = range
        guard let rangeValue = AXValueCreate(.cfRange, &rangeCopy) else { return nil }
        var boundsObj: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(
                element, kAXBoundsForRangeParameterizedAttribute as CFString, rangeValue, &boundsObj) == .success,
              let boundsRef = boundsObj, CFGetTypeID(boundsRef) == AXValueGetTypeID() else { return nil }
        var rect = CGRect.zero
        guard AXValueGetValue(boundsRef as! AXValue, .cgRect, &rect), rect.height > 0 else { return nil }

        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        let flippedY = primaryHeight - rect.origin.y - rect.height
        return NSRect(x: rect.origin.x, y: flippedY, width: max(rect.width, 2), height: rect.height)
    }

    private func startTimer() {
        stopTimer()
        timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self, let t0 = self.startedAt else { return }
            self.timeLabel.stringValue = String(format: "%.1fs", Date().timeIntervalSince(t0))
        }
        if let timer { RunLoop.main.add(timer, forMode: .common) }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func startPulse() {
        stopPulse()
        var up = false
        pulseTimer = Timer.scheduledTimer(withTimeInterval: 0.7, repeats: true) { [weak self] _ in
            guard let self else { return }
            up.toggle()
            NSAnimationContext.runAnimationGroup { ctx in
                ctx.duration = 0.65
                self.iconView.animator().alphaValue = up ? 0.55 : 1.0
            }
        }
        if let pulseTimer { RunLoop.main.add(pulseTimer, forMode: .common) }
    }

    private func stopPulse() {
        pulseTimer?.invalidate()
        pulseTimer = nil
        iconView.alphaValue = 1
    }
}

final class WaveView: NSView {
    private var levels: [Float] = []
    private let capacity = 28
    var barColor: NSColor = UITheme.record

    func push(_ level: Float) {
        levels.append(min(max(level, 0), 1))
        if levels.count > capacity { levels.removeFirst(levels.count - capacity) }
        needsDisplay = true
    }

    func reset() {
        levels.removeAll()
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        guard !levels.isEmpty else { return }
        let barW: CGFloat = 2.6
        let gap: CGFloat = (bounds.width - CGFloat(capacity) * barW) / CGFloat(max(capacity - 1, 1))
        let midY = bounds.midY
        for (i, lv) in levels.enumerated() {
            let age = CGFloat(i) / CGFloat(max(levels.count - 1, 1))
            let alpha = 0.35 + 0.65 * age
            barColor.withAlphaComponent(alpha).setFill()
            let h = max(3, CGFloat(sqrt(lv)) * (bounds.height - 4))
            let x = bounds.minX + CGFloat(i) * (barW + gap)
            let bar = NSRect(x: x, y: midY - h / 2, width: barW, height: h)
            NSBezierPath(roundedRect: bar, xRadius: 1.3, yRadius: 1.3).fill()
        }
    }
}
