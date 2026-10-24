import Foundation
import XCTest
@testable import OpenDictate

/// 殼 v0.9：逾時不丟（SocketClient 慢了先通知再繼續等）、晚到的句子打字或放剪貼簿、重轉按鈕何時可按、
/// daemon 結果檔、重轉請求、統計不把重轉算成新的一句。不開 GUI、不碰麥克風、不連正式 daemon。
final class ShellV090Tests: XCTestCase {

    // MARK: - SocketClient：慢了先通知、再繼續等

    func testSlowAnswerStillArrivesAfterTheSlowNotice() throws {
        let server = try FakeDaemon(delay: 1.2, reply: #"{"ok": true, "text": "晚到的字", "uid": "abc123def456"}"#)
        defer { server.stop() }
        var slowCalls = 0
        let r = try SocketClient(socketPath: server.path, timeout: 5)
            .roundTrip(.transcribe(wavPath: "/tmp/x.wav", punct: "smart_zh"), slowAfter: 0.3) { slowCalls += 1 }
        XCTAssertEqual(slowCalls, 1)
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.text, "晚到的字")
        XCTAssertEqual(r.uid, "abc123def456")
    }

    func testFastAnswerNeverCallsSlow() throws {
        let server = try FakeDaemon(delay: 0, reply: #"{"ok": true, "pong": true}"#)
        defer { server.stop() }
        var slowCalls = 0
        let r = try SocketClient(socketPath: server.path, timeout: 5).roundTrip(.ping, slowAfter: 1) { slowCalls += 1 }
        XCTAssertTrue(r.pong)
        XCTAssertEqual(slowCalls, 0)
    }

    func testGivesUpOnlyAtTheHardTimeout() throws {
        let server = try FakeDaemon(delay: 3, reply: #"{"ok": true}"#)
        defer { server.stop() }
        var slowCalls = 0
        let started = Date()
        XCTAssertThrowsError(try SocketClient(socketPath: server.path, timeout: 1.0)
            .roundTrip(.ping, slowAfter: 0.3) { slowCalls += 1 }) { error in
            guard case SocketClientError.timeout = error else { return XCTFail("expected timeout, got \(error)") }
        }
        let waited = Date().timeIntervalSince(started)
        XCTAssertEqual(slowCalls, 1)
        XCTAssertGreaterThan(waited, 0.9, "kept waiting past slowAfter")
        XCTAssertLessThan(waited, 2.0, "but not past the hard timeout")
    }

    func testWithoutSlowAfterTheTimeoutIsUnchanged() throws {
        let server = try FakeDaemon(delay: 2, reply: #"{"ok": true}"#)
        defer { server.stop() }
        XCTAssertThrowsError(try SocketClient(socketPath: server.path, timeout: 0.5).roundTrip(.ping))
    }

    // MARK: - 晚到的句子：打字還是放剪貼簿

    func testOnTimeResultIsTypedWhereverTheCursorIs() {
        let t0 = Date()
        XCTAssertNil(DeliveryPolicy.holdBack(jobID: 3, lastDeliveredJobID: 2, late: false, targetPID: 10, frontPID: 99,
                                             releasedAt: t0, lastUserInputAt: t0.addingTimeInterval(1)))
    }

    func testOvertakenResultNeverJumpsTheQueue() {
        let t0 = Date()
        XCTAssertNotNil(DeliveryPolicy.holdBack(jobID: 2, lastDeliveredJobID: 3, late: false, targetPID: 10, frontPID: 10,
                                                releasedAt: t0, lastUserInputAt: nil))
    }

    func testLateResultIsTypedOnlyIfNothingMoved() {
        let t0 = Date()
        XCTAssertNil(DeliveryPolicy.holdBack(jobID: 4, lastDeliveredJobID: 3, late: true, targetPID: 10, frontPID: 10,
                                             releasedAt: t0, lastUserInputAt: t0.addingTimeInterval(-5)))
        XCTAssertEqual(DeliveryPolicy.holdBack(jobID: 4, lastDeliveredJobID: 3, late: true, targetPID: 10, frontPID: 11,
                                               releasedAt: t0, lastUserInputAt: nil), "已經換到別的視窗")
        XCTAssertEqual(DeliveryPolicy.holdBack(jobID: 4, lastDeliveredJobID: 3, late: true, targetPID: 10, frontPID: 10,
                                               releasedAt: t0, lastUserInputAt: t0.addingTimeInterval(2)),
                       "放開之後你已經接著打字了")
    }

    // MARK: - daemon 結果檔

    func testResultFileIsFoundByWavPath() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("dictation-last.json")
        let body = #"""
        {"version": 1, "results": [
          {"uid": "bbbbbb000002", "wav": "/tmp/rec-2.wav", "ok": false, "error": "no_speech"},
          {"uid": "aaaaaa000001", "wav": "/tmp/rec-1.wav", "ok": true, "text": "撿回來的字", "changes": [["a", "b"]], "total_ms": 21000}
        ]}
        """#
        try body.write(to: file, atomically: true, encoding: .utf8)
        let saved = DaemonResults.path
        DaemonResults.path = file.path
        defer { DaemonResults.path = saved }

        let r = try XCTUnwrap(DaemonResults.find(wavPath: "/tmp/rec-1.wav"))
        XCTAssertTrue(r.ok)
        XCTAssertEqual(r.text, "撿回來的字")
        XCTAssertEqual(r.uid, "aaaaaa000001")
        XCTAssertEqual(r.changes ?? [], [["a", "b"]])
        XCTAssertEqual(DaemonResults.find(wavPath: "/tmp/rec-2.wav")?.error, "no_speech")
        XCTAssertNil(DaemonResults.find(wavPath: "/tmp/rec-3.wav"))
    }

    // MARK: - 重轉

    func testRetranscribeRequest() throws {
        let line = try DaemonRequest.retranscribe(uid: "abc123def456", punct: "smart_zh", fillers: "strip").jsonLine()
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: line.dropLast()) as? [String: String])
        XCTAssertEqual(obj, ["cmd": "retranscribe", "uid": "abc123def456", "mode": "accurate",
                             "punct": "smart_zh", "fillers": "strip"])
    }

    func testRedoButtonExplainsWhyItIsOff() {
        let m = ControlCenterModel()
        m.mode = "fast"
        m.daemonOnline = true
        let now = Date()
        let fresh = ControlCenterModel.Utterance(text: "快模式的字", raw: nil, changes: [], latencyMs: 800,
                                                 at: now.addingTimeInterval(-10), uid: "abc123def456")
        m.recentAudioSeconds = nil
        XCTAssertNotNil(m.redoBlocker(fresh, now: now), "old daemon: no redo")
        m.recentAudioSeconds = 60
        XCTAssertNil(m.redoBlocker(fresh, now: now))
        XCTAssertNotNil(m.redoBlocker(fresh, now: now.addingTimeInterval(55)), "recording expired")
        var noUID = fresh
        noUID.uid = nil
        XCTAssertNotNil(m.redoBlocker(noUID, now: now))
        var redone = fresh
        redone.redoOf = "000000aaaaaa"
        XCTAssertEqual(m.redoBlocker(redone, now: now), "這句已經是準模式的結果")
        m.mode = "accurate"
        XCTAssertEqual(m.redoBlocker(fresh, now: now), "現在就是準模式")
        m.mode = "fast"
        m.daemonOnline = false
        XCTAssertEqual(m.redoBlocker(fresh, now: now), "辨識服務離線")
    }

    func testWithdrawnIsMarkedOnTheRightSentence() {
        let m = ControlCenterModel()
        let a = ControlCenterModel.Utterance(text: "第一句", raw: nil, changes: [], latencyMs: nil, at: Date())
        let b = ControlCenterModel.Utterance(text: "第二句", raw: nil, changes: [], latencyMs: nil, at: Date())
        m.push(a)
        m.push(b)
        m.markWithdrawn(b)
        XCTAssertTrue(m.history[0].withdrawn)
        XCTAssertFalse(m.history[1].withdrawn)
    }

    // MARK: - 統計

    func testStatsSkipRedoRowsButRecentEntriesKeepTheirUID() throws {
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).jsonl")
        defer { try? FileManager.default.removeItem(at: file) }
        let rows = [
            #"{"ts": "2026-10-24T14:00:00", "text": "原本那句", "total_ms": 800, "uid": "aaaaaa000001"}"#,
            #"{"ts": "2026-10-24T14:00:20", "text": "準模式那句", "total_ms": 1500, "uid": "bbbbbb000002", "redo_of": "aaaaaa000001"}"#,
            #"{"ts": "2026-10-24T14:01:00", "text": "測試句", "total_ms": 500, "tag": "selftest"}"#,
        ]
        try (rows.joined(separator: "\n") + "\n").write(to: file, atomically: true, encoding: .utf8)
        let stats = DictationStats.load(from: file)
        XCTAssertEqual(stats.okCount, 1)
        XCTAssertEqual(stats.chars, "原本那句".count)
        let recent = DictationStats.recentSuccessEntries(limit: 5, from: file)
        XCTAssertEqual(recent.map(\.uid), ["bbbbbb000002", "aaaaaa000001"])
        XCTAssertEqual(recent.first?.redoOf, "aaaaaa000001")
    }
}

/// 一次回一句的假 daemon（unix socket，背景執行緒）：讀一行請求、等 delay 秒、回 reply。
private final class FakeDaemon {
    let path: String
    private let fd: Int32
    private var stopped = false

    init(delay: TimeInterval, reply: String) throws {
        path = "/tmp/od-test-\(getpid())-\(UInt32.random(in: 0...UInt32.max)).sock"
        unlink(path)
        fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        withUnsafeMutableBytes(of: &addr.sun_path) { dst in
            path.utf8CString.withUnsafeBytes { dst.copyBytes(from: $0) }
        }
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard bound == 0, listen(fd, 4) == 0 else { throw SocketClientError.internalError("bind/listen") }
        let listener = fd
        Thread.detachNewThread { [weak self] in
            while self?.stopped == false {
                let conn = accept(listener, nil, nil)
                guard conn >= 0 else { return }
                var buf = [UInt8](repeating: 0, count: 4096)
                _ = recv(conn, &buf, buf.count, 0)
                Thread.sleep(forTimeInterval: delay)
                let out = Array((reply + "\n").utf8)
                _ = out.withUnsafeBytes { send(conn, $0.baseAddress, $0.count, 0) }
                close(conn)
            }
        }
    }

    func stop() {
        stopped = true
        close(fd)
        unlink(path)
    }
}
