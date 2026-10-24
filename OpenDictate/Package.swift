// swift-tools-version:5.9
// OpenDictate — fn push-to-talk 語音輸入殼（menubar app）
// 協議 SSOT：../IO-CONTRACT.md
import PackageDescription

let package = Package(
    name: "OpenDictate",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        .executableTarget(
            name: "OpenDictate",
            path: "Sources/OpenDictate"
        ),
        // 不開 GUI、不碰麥克風的邏輯測試：swift test
        .testTarget(
            name: "OpenDictateTests",
            dependencies: ["OpenDictate"],
            path: "Tests/OpenDictateTests"
        )
    ]
)
