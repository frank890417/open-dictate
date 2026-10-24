import AppKit
import SwiftUI

/// 選單列控制中心（左鍵）：狀態、辨識模式、常用開關、上一句、最近紀錄、今日數字。
/// 右鍵仍是完整的文字選單（StatusItemController 的 classicMenu）。
///
/// 為什麼長這樣（2026-10 一個月的使用紀錄）：
/// - 模式只切過 2 次、詞庫回報 0 次——藏在第二層子選單裡的東西不會被用到，常用的要在第一眼。
/// - 他最常做的是「接著上一句講」、偶爾要把上一句再拿出來：上一句是一張卡，複製／再貼一次／教詞庫都在卡上。
/// - v0.9：準確模式一個月 0 句、聽錯時不換模式而是抱怨——「準」變成上一句卡上的「用準模式重轉」，旁邊是「撤回」；
///   晚到沒貼上的那句單獨一張卡，一鍵貼回去。
final class ControlCenterModel: ObservableObject {

    struct Utterance: Identifiable, Equatable {
        let id = UUID()
        let text: String
        let raw: String?
        let changes: [[String]]
        let latencyMs: Int?
        let at: Date
        /// daemon 給的 id（v0.9）：用準模式重轉要它
        var uid: String? = nil
        /// 這句是哪一句的重轉結果
        var redoOf: String? = nil
        /// 已從文件裡撤回
        var withdrawn = false
    }

    enum Phase { case idle, recording, transcribing, error }

    @Published var phase: Phase = .idle
    @Published var daemonOnline: Bool?            // nil＝檢查中
    @Published var daemonDetail = "檢查中…"
    @Published var mode: String?                  // fast／accurate；nil＝舊 daemon 或離線
    @Published var punctMode = DictateSettings.punctMode
    @Published var stripFillers = DictateSettings.stripFillers
    @Published var hudEnabled = DictateSettings.hudEnabled
    @Published var showRawDiff = DictateSettings.showRawDiff
    @Published var hotkey = DictateSettings.hotkey
    @Published var micName = ""
    @Published var micFollowsSystem = true
    @Published var history: [Utterance] = []       // [0]＝上一句
    @Published var stats = DictationStats.loadToday()
    @Published var notice: String?                 // 最近一次錯誤（麥克風靜音、daemon 沒回應…）
    @Published var copiedID: UUID?
    @Published var meetingAvailable = false
    /// 會議 app 正在錄（ProductConfig.meetingStatusFile 存在）；nil＝沒在錄或不知道
    @Published var meeting: MeetingStatus?
    /// 偵測到正在通話的會議 App（MeetingDetector）；nil＝沒有
    @Published var meetingDetected: String?

    struct MeetingStatus: Equatable {
        let title: String
        let startedAt: Date?
    }
    /// 今天 3 題（LexiconQuiz）；nil＝這個產品沒有出題腳本或讀不到
    @Published var quiz: LexiconQuiz.Snapshot?
    @Published var answering: Set<String> = []
    @Published var transcribingLabel = "轉錄中…"   // 久了變「還在轉…」
    @Published var queued = 0                     // 送出還沒回來的句數（連講時可以 >1）
    @Published var recentAudioSeconds: Double?    // daemon 留錄音幾秒可以重轉；nil＝不能重轉
    @Published var redoBusy = false
    @Published var undelivered: Utterance?        // 晚到、焦點換了沒貼上的那句

    // 由 StatusItemController 接線
    var onSetMode: (String) -> Void = { _ in }
    var onPopMenu: (MenuKind) -> Void = { _ in }
    var onHotkeyChanged: () -> Void = {}
    var onRepaste: (String) -> Void = { _ in }
    var onTeach: () -> Void = {}
    var onReloadLexicon: () -> Void = {}
    var onRestartDaemon: () -> Void = {}
    var onOpenLog: () -> Void = {}
    var onOpenSettings: () -> Void = {}
    var onPermissions: () -> Void = {}
    var onStartMeeting: () -> Void = {}
    var onAnswerQuiz: (String, Bool) -> Void = { _, _ in }
    var onRetranscribe: (Utterance) -> Void = { _ in }
    var onWithdraw: (Utterance) -> Void = { _ in }
    var onPasteUndelivered: (Utterance) -> Void = { _ in }

    enum MenuKind { case mic, punct, teach }

    var last: Utterance? { history.first }

    /// 開控制中心前重讀一次（設定可能在設定窗或完整選單被改過）。
    func refresh() {
        punctMode = DictateSettings.punctMode
        stripFillers = DictateSettings.stripFillers
        hudEnabled = DictateSettings.hudEnabled
        showRawDiff = DictateSettings.showRawDiff
        hotkey = DictateSettings.hotkey
        micName = MicDevice.resolveRecordingDevice().name   // 這次錄音實際會用的那支（含闔蓋改用外接的判斷）
        micFollowsSystem = DictateSettings.preferredMicUID == nil
        stats = DictationStats.loadToday()
        meeting = Self.readMeetingStatus()
        meetingAvailable = URL(string: ProductConfig.meetingURL)
            .flatMap { NSWorkspace.shared.urlForApplication(toOpen: $0) } != nil
    }

    /// 狀態檔（會議 app 錄音中才有）：{"title": …, "started_at": ISO8601 或 epoch 秒, …}
    static func readMeetingStatus() -> MeetingStatus? {
        guard let path = ProductConfig.meetingStatusFile,
              let data = FileManager.default.contents(atPath: path) else { return nil }
        let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        var started: Date?
        if let n = obj["started_at"] as? NSNumber {
            started = Date(timeIntervalSince1970: n.doubleValue)
        } else if let s = obj["started_at"] as? String {
            let f = ISO8601DateFormatter()
            f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            started = f.date(from: s) ?? ISO8601DateFormatter().date(from: s)
        }
        return MeetingStatus(title: obj["title"] as? String ?? "", startedAt: started)
    }

    func push(_ u: Utterance) {
        if let first = history.first, first.text == u.text { return }
        history.insert(u, at: 0)
        if history.count > 8 { history.removeLast(history.count - 8) }
    }

    func markWithdrawn(_ u: Utterance) {
        guard let i = history.firstIndex(where: { $0.id == u.id }) else { return }
        history[i].withdrawn = true
    }

    /// 上一句不能用準模式重轉的原因（nil＝可以）：按鈕變灰、滑過去看得到為什麼
    func redoBlocker(_ u: Utterance, now: Date = Date()) -> String? {
        guard let keep = recentAudioSeconds else { return "辨識服務不支援重轉（要 daemon 0.9 的快／準模式）" }
        if daemonOnline == false { return "辨識服務離線" }
        if u.redoOf != nil { return "這句已經是準模式的結果" }
        if mode == "accurate" { return "現在就是準模式" }
        guard u.uid != nil else { return "這句沒有錄音編號（辨識服務重啟前的句子）" }
        if now.timeIntervalSince(u.at) > keep - 1 { return "錄音只留 \(Int(keep)) 秒，已經刪掉了" }
        return nil
    }

    func copy(_ u: Utterance) {
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(u.text, forType: .string)
        copiedID = u.id
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            if self?.copiedID == u.id { self?.copiedID = nil }
        }
    }

    func toggleFillers() { stripFillers.toggle(); DictateSettings.stripFillers = stripFillers }
    func toggleHUD() { hudEnabled.toggle(); DictateSettings.hudEnabled = hudEnabled }
    func toggleRawDiff() { showRawDiff.toggle(); DictateSettings.showRawDiff = showRawDiff }
    func cycleHotkey() {
        hotkey = hotkey == "fn" ? "rightOption" : "fn"
        DictateSettings.hotkey = hotkey
        onHotkeyChanged()
    }

    var hotkeyLabel: String { hotkey == "rightOption" ? "右 ⌥" : "fn" }

    var punctLabel: String {
        if mode == "fast" && punctMode != "raw" { return "模型自帶" }
        switch punctMode {
        case "llm_zh": return "LLM 完整"
        case "raw": return "原樣"
        default: return "智慧全形"
        }
    }
}

// MARK: - View

struct ControlCenterView: View {
    @ObservedObject var model: ControlCenterModel
    /// 截圖用（ImageRenderer 畫不出毛玻璃）：給一個實心底
    var solidBackground = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if let notice = model.notice { noticeBanner(notice) }
            modeTiles
            toggleGrid
            HStack(spacing: 8) {
                WideTile(symbol: "mic.fill", title: model.micFollowsSystem ? "麥克風・跟系統" : "麥克風・指定",
                         value: model.micName.isEmpty ? "系統預設" : model.micName) {
                    model.onPopMenu(.mic)
                }
                WideTile(symbol: "text.quote", title: "標點", value: model.punctLabel) {
                    model.onPopMenu(.punct)
                }
                .disabled(model.mode == "fast")
                .help(model.mode == "fast" ? "快速模式用模型自帶的標點；切到「準」才能選" : "標點模式")
            }
            if let u = model.undelivered { undeliveredCard(u) }
            lastCard
            if let quiz = model.quiz, !quiz.questions.isEmpty { quizCard(quiz) }
            if model.history.count > 1 { historyList }
            statsRow
            meetingButton
            footer
        }
        .padding(14)
        .frame(width: 340)
        .background(solidBackground ? Color(nsColor: .windowBackgroundColor) : Color.clear)
    }

    // MARK: header

    private var phaseColor: Color {
        switch model.phase {
        case .idle: return model.daemonOnline == false ? .gray : .accentColor
        case .recording: return .red
        case .transcribing, .error: return .orange
        }
    }

    private var phaseSymbol: String {
        switch model.phase {
        case .idle, .recording: return "mic.fill"
        case .transcribing: return "waveform"
        case .error: return "exclamationmark.triangle.fill"
        }
    }

    private var phaseText: String {
        switch model.phase {
        case .idle: return "按住 \(model.hotkeyLabel) 講話，放開就打字"
        case .recording: return model.queued > 0 ? "錄音中…（上一句還在轉）" : "錄音中…"
        case .transcribing: return model.queued > 1 ? "\(model.transcribingLabel)（\(model.queued) 句）" : model.transcribingLabel
        case .error: return "剛剛出了點問題"
        }
    }

    private var header: some View {
        HStack(spacing: 10) {
            ZStack {
                Circle().fill(phaseColor.gradient).frame(width: 34, height: 34)
                Image(systemName: phaseSymbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(ProductConfig.appName).font(.system(size: 14, weight: .semibold))
                Text(phaseText).font(.system(size: 11)).foregroundStyle(.secondary)
                    .lineLimit(1).minimumScaleFactor(0.75)
            }
            Spacer(minLength: 4)
            HStack(spacing: 5) {
                Circle()
                    .fill(model.daemonOnline == nil ? Color.gray : (model.daemonOnline! ? Color.green : Color.red))
                    .frame(width: 7, height: 7)
                Text(model.daemonOnline == nil ? "檢查中" : (model.daemonOnline! ? "線上" : "離線"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Capsule().fill(Color.primary.opacity(0.06)))
            .help(model.daemonDetail)
            IconButton(symbol: "gearshape", help: "設定…", action: model.onOpenSettings)
        }
    }

    private func noticeBanner(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
            Text(text).font(.system(size: 11)).lineLimit(3)
            Spacer(minLength: 0)
            Button { model.notice = nil } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                .buttonStyle(.plain).foregroundStyle(.secondary)
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color.orange.opacity(0.12)))
    }

    // MARK: mode & toggles

    private var modeTiles: some View {
        HStack(spacing: 8) {
            ModeTile(symbol: "bolt.fill", title: "快", subtitle: "Qwen3・約 1 秒", selected: model.mode == "fast") {
                model.onSetMode("fast")
            }
            ModeTile(symbol: "scope", title: "準", subtitle: "Whisper・較準", selected: model.mode == "accurate") {
                model.onSetMode("accurate")
            }
        }
        .disabled(model.mode == nil)
        .opacity(model.mode == nil ? 0.5 : 1)
    }

    private var toggleGrid: some View {
        HStack(spacing: 8) {
            ToggleTile(symbol: "text.badge.minus", title: "去掉呃嗯", on: model.stripFillers,
                       help: "拿掉「呃」「嗯」等語氣聲（原文仍留在記錄）", action: model.toggleFillers)
            ToggleTile(symbol: "text.bubble", title: "浮動提示", on: model.hudEnabled,
                       help: "錄音時在游標旁顯示波形與結果", action: model.toggleHUD)
            ToggleTile(symbol: "arrow.left.arrow.right", title: "原文對照", on: model.showRawDiff,
                       help: "成功時在浮動提示顯示辨識原文／詞庫改了哪裡", action: model.toggleRawDiff)
            ToggleTile(symbol: model.hotkey == "fn" ? "globe" : "option", title: "熱鍵 \(model.hotkeyLabel)", on: false,
                       help: "點一下切換 fn／右 Option", action: model.cycleHotkey)
        }
    }

    // MARK: last & history

    private func meta(_ u: ControlCenterModel.Utterance) -> String {
        var bits = [Self.clock.string(from: u.at)]
        if let ms = u.latencyMs { bits.append(ms >= 1000 ? String(format: "%.1f 秒", Double(ms) / 1000) : "\(ms) ms") }
        if !u.changes.isEmpty { bits.append("校正 \(u.changes.count) 處") }
        bits.append("\(u.text.count) 字")
        return bits.joined(separator: " · ")
    }

    private var lastCard: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Text("上一句").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                if let u = model.last { Text(meta(u)).font(.system(size: 11)).foregroundStyle(.tertiary) }
                Spacer()
                if let u = model.last, u.withdrawn {
                    Badge(text: "已撤回", color: .secondary)
                } else if let u = model.last, u.redoOf != nil {
                    Badge(text: "準模式", color: .accentColor)
                }
            }
            if let u = model.last {
                Text(u.text)
                    .font(.system(size: 12.5))
                    .strikethrough(u.withdrawn)
                    .foregroundStyle(u.withdrawn ? .secondary : .primary)
                    .lineLimit(5)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
                // 聽錯了的兩條路：換成準模式的結果、或整句拿掉
                HStack(spacing: 6) {
                    let blocker = model.redoBlocker(u)
                    PillButton(symbol: model.redoBusy ? "hourglass" : "scope",
                               title: model.redoBusy ? "重轉中…" : "用準模式重轉", prominent: true) {
                        model.onRetranscribe(u)
                    }
                    .disabled(blocker != nil || model.redoBusy || u.withdrawn)
                    .help(blocker ?? "用 Whisper 把剛才那段錄音再轉一次，換掉打進去的字")
                    PillButton(symbol: "arrow.uturn.backward", title: u.withdrawn ? "已撤回" : "撤回") {
                        model.onWithdraw(u)
                    }
                    .disabled(u.withdrawn || model.redoBusy)
                    .help("把剛打進去的這句刪掉（打進去之後有打字或換了視窗就只提示、不動文件）")
                    Spacer(minLength: 0)
                }
                HStack(spacing: 6) {
                    PillButton(symbol: model.copiedID == u.id ? "checkmark" : "doc.on.doc",
                               title: model.copiedID == u.id ? "已複製" : "複製") { model.copy(u) }
                    PillButton(symbol: "arrow.uturn.down", title: "再貼一次") { model.onRepaste(u.text) }
                    PillButton(symbol: "character.book.closed", title: "教詞庫") { model.onPopMenu(.teach) }
                    Spacer(minLength: 0)
                }
            } else {
                Text("還沒有聽寫。按住 \(model.hotkeyLabel) 開始講話。")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
            }
        }
        .tile()
    }

    // MARK: quiz（今天 3 題）

    private func quizCard(_ quiz: LexiconQuiz.Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Image(systemName: quiz.pendingToday == 0 ? "checkmark.seal.fill" : "character.book.closed.fill")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(quiz.pendingToday == 0 ? Color.green : Color.accentColor)
                Text(quiz.pendingToday == 0 ? "詞庫題：今天答完了" : "今天 \(quiz.questions.count) 題：以後都這樣改？")
                    .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                if quiz.pendingTotal > 0 {
                    Text("還有 \(quiz.pendingTotal) 題排隊").font(.system(size: 10.5)).foregroundStyle(.tertiary)
                }
            }
            if quiz.pendingToday > 0 {
                ForEach(quiz.questions) { q in quizRow(q) }
            }
            ForEach(quiz.staleNotes, id: \.self) { note in
                Text("⚠️ \(note)").font(.system(size: 10.5)).foregroundStyle(.orange)
            }
        }
        .tile()
    }

    private func quizRow(_ q: LexiconQuiz.Question) -> some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(q.wrong).strikethrough(q.status == "yes").foregroundStyle(.secondary)
                    Image(systemName: "arrow.right").font(.system(size: 9, weight: .bold)).foregroundStyle(.tertiary)
                    Text(q.right).fontWeight(.semibold)
                }
                .font(.system(size: 12.5))
                if !q.sample.isEmpty {
                    Text(q.sample).font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            .help(q.question)
            Spacer(minLength: 4)
            if !q.isPending {
                Image(systemName: q.status == "yes" ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .font(.system(size: 15))
                    .foregroundStyle(q.status == "yes" ? Color.green : Color.secondary)
            } else if model.answering.contains(q.id) {
                ProgressView().controlSize(.small)
            } else {
                ChoiceButton(title: "是", primary: true) { model.onAnswerQuiz(q.id, true) }
                ChoiceButton(title: "否", primary: false) { model.onAnswerQuiz(q.id, false) }
            }
        }
    }

    private func undeliveredCard(_ u: ControlCenterModel.Utterance) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                Image(systemName: "tray.and.arrow.down.fill").font(.system(size: 11)).foregroundStyle(.orange)
                Text("剛剛沒送到的那句").font(.system(size: 11, weight: .semibold))
                Text(Self.clock.string(from: u.at)).font(.system(size: 11)).foregroundStyle(.tertiary)
                Spacer()
                Button { model.undelivered = nil } label: { Image(systemName: "xmark").font(.system(size: 9, weight: .bold)) }
                    .buttonStyle(.plain).foregroundStyle(.secondary).help("不用了")
            }
            Text(u.text)
                .font(.system(size: 12))
                .lineLimit(3)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                PillButton(symbol: "arrow.uturn.down", title: "貼上", prominent: true) { model.onPasteUndelivered(u) }
                    .help("貼到你剛剛在用的地方")
                PillButton(symbol: model.copiedID == u.id ? "checkmark" : "doc.on.doc",
                           title: model.copiedID == u.id ? "已複製" : "複製") { model.copy(u) }
                Spacer(minLength: 0)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.orange.opacity(0.12)))
    }

    private var historyList: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("最近（點一下複製）").font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                .padding(.bottom, 2)
            ForEach(model.history.dropFirst().prefix(5)) { u in
                Button { model.copy(u) } label: {
                    HStack(spacing: 8) {
                        Text(Self.clock.string(from: u.at))
                            .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(.tertiary)
                        Text(u.text).font(.system(size: 11.5)).lineLimit(1)
                        Spacer(minLength: 0)
                        if model.copiedID == u.id {
                            Image(systemName: "checkmark").font(.system(size: 10, weight: .bold)).foregroundStyle(.green)
                        }
                    }
                    .padding(.vertical, 3)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(u.text)
            }
        }
        .tile()
    }

    // MARK: stats, meeting, footer

    private var statsRow: some View {
        let s = model.stats
        let speed = s.p50Ms.map { $0 >= 1000 ? String(format: "%.1f 秒", Double($0) / 1000) : "\($0) ms" } ?? "—"
        return HStack(spacing: 8) {
            StatTile(value: "\(s.okCount)", label: "今日句數")
            StatTile(value: s.chars >= 10000 ? String(format: "%.1f 萬", Double(s.chars) / 10000) : "\(s.chars)", label: "字數")
            StatTile(value: speed, label: "中位速度")
            StatTile(value: "\(s.noSpeech)", label: "沒聽到話")
        }
    }

    private var meetingButton: some View {
        Button(action: model.onStartMeeting) {
            HStack(spacing: 10) {
                Image(systemName: model.meeting != nil ? "stop.circle.fill" : "record.circle")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(model.meetingAvailable || model.meeting != nil ? Color.red : Color.secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(model.meeting != nil ? "會議錄音中・點一下結束" : "會議錄音")
                        .font(.system(size: 12.5, weight: .semibold))
                    if let m = model.meeting {
                        TimelineView(.periodic(from: .now, by: 1)) { ctx in
                            Text(meetingSubtitle(m, now: ctx.date))
                                .font(.system(size: 10.5).monospacedDigit()).foregroundStyle(.secondary)
                        }
                    } else if let app = model.meetingDetected, model.meetingAvailable {
                        Text("偵測到 \(app) 正在通話 · 點這裡開始錄")
                            .font(.system(size: 10.5, weight: .medium)).foregroundStyle(Color.red)
                    } else {
                        Text(model.meetingAvailable ? "邊錄邊出逐字稿與即時重點" : "即時會議面板未安裝")
                            .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if model.meetingAvailable { Image(systemName: "chevron.right").foregroundStyle(.tertiary) }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(model.meeting != nil ? Color.red.opacity(0.14) : Color.primary.opacity(0.06)))
        .disabled(!model.meetingAvailable)
    }

    private func meetingSubtitle(_ m: ControlCenterModel.MeetingStatus, now: Date) -> String {
        var bits: [String] = []
        if !m.title.isEmpty { bits.append(m.title) }
        if let t0 = m.startedAt {
            let s = max(0, Int(now.timeIntervalSince(t0)))
            bits.append(s >= 3600 ? String(format: "已錄 %d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                                  : String(format: "已錄 %d:%02d", s / 60, s % 60))
        }
        return bits.isEmpty ? "錄音中" : bits.joined(separator: " · ")
    }

    private var footer: some View {
        HStack(spacing: 2) {
            IconButton(symbol: "text.book.closed", help: "重載詞庫", action: model.onReloadLexicon)
            IconButton(symbol: "arrow.clockwise", help: "重啟辨識服務（daemon）", action: model.onRestartDaemon)
            IconButton(symbol: "doc.text", help: "開啟今日聽寫記錄", action: model.onOpenLog)
            IconButton(symbol: "lock.shield", help: "權限說明", action: model.onPermissions)
            Spacer()
            Text("右鍵圖示：完整選單").font(.system(size: 10)).foregroundStyle(.tertiary)
            IconButton(symbol: "power", help: "結束 \(ProductConfig.appName)") { NSApp.terminate(nil) }
        }
    }

    static let clock: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()
}

// MARK: - Pieces

private struct TileBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.06)))
    }
}

private extension View {
    func tile() -> some View { modifier(TileBackground()) }
}

private struct ModeTile: View {
    let symbol: String
    let title: String
    let subtitle: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                ZStack {
                    Circle().fill(selected ? Color.white.opacity(0.25) : Color.primary.opacity(0.08))
                        .frame(width: 30, height: 30)
                    Image(systemName: symbol).font(.system(size: 13, weight: .semibold))
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.system(size: 14, weight: .bold))
                    Text(subtitle).font(.system(size: 10.5)).opacity(0.8).lineLimit(1).minimumScaleFactor(0.8)
                }
                Spacer(minLength: 0)
            }
            .foregroundStyle(selected ? Color.white : Color.primary)
            .padding(10)
            .frame(maxWidth: .infinity)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(selected ? AnyShapeStyle(Color.accentColor.gradient) : AnyShapeStyle(Color.primary.opacity(0.06))))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

private struct ToggleTile: View {
    let symbol: String
    let title: String
    let on: Bool
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                ZStack {
                    Circle().fill(on ? AnyShapeStyle(Color.accentColor.gradient) : AnyShapeStyle(Color.primary.opacity(0.1)))
                        .frame(width: 30, height: 30)
                    Image(systemName: symbol).font(.system(size: 12.5, weight: .semibold))
                        .foregroundStyle(on ? Color.white : Color.primary)
                }
                Text(title).font(.system(size: 10.5)).lineLimit(1).minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.06)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(help)
    }
}

private struct WideTile: View {
    let symbol: String
    let title: String
    let value: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).frame(width: 16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.system(size: 10.5)).foregroundStyle(.secondary)
                    Text(value).font(.system(size: 12, weight: .medium)).lineLimit(1)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .semibold)).foregroundStyle(.tertiary)
            }
            .padding(10)
            .frame(maxWidth: .infinity)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.06)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

private struct StatTile: View {
    let value: String
    let label: String

    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(.system(size: 14, weight: .semibold).monospacedDigit()).lineLimit(1).minimumScaleFactor(0.7)
            Text(label).font(.system(size: 10)).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.primary.opacity(0.06)))
    }
}

private struct ChoiceButton: View {
    let title: String
    let primary: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11.5, weight: .semibold))
                .foregroundStyle(primary ? Color.white : Color.primary)
                .frame(width: 30, height: 22)
                .background(Capsule().fill(primary ? AnyShapeStyle(Color.accentColor.gradient) : AnyShapeStyle(Color.primary.opacity(0.1))))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

private struct PillButton: View {
    let symbol: String
    let title: String
    var prominent = false
    let action: () -> Void
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: symbol).font(.system(size: 10.5, weight: .semibold))
                Text(title).font(.system(size: 11, weight: .medium))
            }
            .foregroundStyle(prominent && isEnabled ? Color.accentColor : Color.primary)
            .padding(.horizontal, 9).padding(.vertical, 5)
            .background(Capsule().fill(prominent && isEnabled ? Color.accentColor.opacity(0.16) : Color.primary.opacity(0.09)))
            .contentShape(Capsule())
            .opacity(isEnabled ? 1 : 0.45)
        }
        .buttonStyle(.plain)
    }
}

private struct Badge: View {
    let text: String
    let color: Color

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.14)))
    }
}

private struct IconButton: View {
    let symbol: String
    let help: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 12, weight: .medium))
                .frame(width: 26, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(.secondary)
        .help(help)
    }
}
