import Foundation
import XCTest
@testable import OpenDictate

/// 會議偵測的純邏輯：誰算會議 App、連續多久才問、同一場只問一次、隔多久算下一場。
final class MeetingDetectorTests: XCTestCase {

    func testMeetingAppsMatchByPrefixIncludingHelpers() {
        XCTAssertEqual(MeetingDetector.meetingAppName(for: "us.zoom.xos"), "Zoom")
        XCTAssertEqual(MeetingDetector.meetingAppName(for: "com.google.Chrome.helper"), "Chrome")
        XCTAssertEqual(MeetingDetector.meetingAppName(for: "com.microsoft.teams2"), "Teams")
        XCTAssertEqual(MeetingDetector.meetingAppName(for: "com.tinyspeck.slackmacgap.helper"), "Slack")
    }

    func testRecordingAndCreativeAppsAreNotMeetings() {
        for id in ["com.obsproject.obs-studio", "ca.derivative.TouchDesigner", "com.anthropic.claudefordesktop",
                   "com.muse.dictate.shell", "com.muse.meeting", "", "us.zoom.xosx"] {
            XCTAssertNil(MeetingDetector.meetingAppName(for: id), id)
        }
    }

    func testAsksOnceAfterSustainedUse() {
        var d = MeetingDetector.Debouncer()
        let t0 = Date(timeIntervalSince1970: 1_000)
        XCTAssertNil(d.feed(["Zoom"], at: t0))
        XCTAssertNil(d.feed(["Zoom"], at: t0.addingTimeInterval(10)))
        XCTAssertEqual(d.feed(["Zoom"], at: t0.addingTimeInterval(20)), "Zoom")
        XCTAssertNil(d.feed(["Zoom"], at: t0.addingTimeInterval(25)))          // 同一場不再問
        XCTAssertEqual(d.active(at: t0.addingTimeInterval(25)), "Zoom")
    }

    func testShortUseNeverAsks() {
        var d = MeetingDetector.Debouncer()
        let t0 = Date(timeIntervalSince1970: 1_000)
        XCTAssertNil(d.feed(["FaceTime"], at: t0))
        XCTAssertNil(d.feed(["FaceTime"], at: t0.addingTimeInterval(15)))
        XCTAssertNil(d.feed([], at: t0.addingTimeInterval(20)))
        XCTAssertNil(d.active(at: t0.addingTimeInterval(20)))
    }

    func testBriefDropIsTheSameMeetingButALongGapIsANewOne() {
        var d = MeetingDetector.Debouncer()
        let t0 = Date(timeIntervalSince1970: 1_000)
        _ = d.feed(["Zoom"], at: t0)
        XCTAssertEqual(d.feed(["Zoom"], at: t0.addingTimeInterval(20)), "Zoom")
        XCTAssertNil(d.feed([], at: t0.addingTimeInterval(40)))                   // 放掉 20 秒（靜音、換裝置）
        XCTAssertNil(d.feed(["Zoom"], at: t0.addingTimeInterval(60)))             // 還是同一場：不再問
        XCTAssertNil(d.feed([], at: t0.addingTimeInterval(200)))                  // 放掉超過 60 秒＝散會
        XCTAssertNil(d.feed(["Zoom"], at: t0.addingTimeInterval(210)))
        XCTAssertEqual(d.feed(["Zoom"], at: t0.addingTimeInterval(230)), "Zoom")  // 下一場再問一次
    }

    func testTwoAppsAreTrackedSeparately() {
        var d = MeetingDetector.Debouncer()
        let t0 = Date(timeIntervalSince1970: 1_000)
        _ = d.feed(["Zoom"], at: t0)
        XCTAssertEqual(d.feed(["Zoom", "Chrome"], at: t0.addingTimeInterval(20)), "Zoom")
        XCTAssertNil(d.feed(["Zoom", "Chrome"], at: t0.addingTimeInterval(30)))
        XCTAssertEqual(d.feed(["Zoom", "Chrome"], at: t0.addingTimeInterval(40)), "Chrome")
    }
}
