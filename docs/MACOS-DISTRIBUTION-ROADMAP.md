# macOS Distribution Roadmap

目標：讓一般使用者從 GitHub Releases 下載 Open Dictate，拖入 Applications、完成系統權限引導後即可使用；不需要 Git、Python、Swift、Xcode Command Line Tools 或保留原始碼 clone。

## 發布原則

- Open Dictate 公開 repo 是通用產品與發布流程的唯一 SSOT。
- Muse Dictate 只保留私人設定與 adapter，使用同一套公開核心及打包管線。
- 語音、逐字稿、個人詞庫、review queue 與 speaker profile 只存在本機 Application Support，不進 App bundle 或 Git history。
- App 必須能在乾淨的受支援 macOS 帳號安裝、更新、移除與回滾。

## Phase 0 — Release architecture and contracts

- [ ] 將品牌、bundle ID、socket、LaunchAgent label、資料路徑與詞庫 provider 抽成 `ProductConfig`。
- [ ] 將 IO contract 做成 machine-readable schema，並讓公開版與私人 overlay 共用 contract tests。
- [ ] 決定最低 macOS 版本、Apple Silicon 支援矩陣與模型相容政策。
- [ ] 將所有 runtime data 移至 `~/Library/Application Support/OpenDictate/`，logs 移至 `~/Library/Logs/OpenDictate/`。
- [ ] 移除對 repo clone 路徑、開發者 venv 與外部 vendor 目錄的執行期依賴。

**完成條件：** 移動或刪除原始碼 clone 後，已安裝的 App 仍可啟動、聽寫、重啟 daemon 並讀取自己的詞庫。

## Phase 1 — Self-contained beta app

- [ ] 把 Python runtime、daemon、MLX/OpenCC 相依套件、starter glossary 與 helper tools 放入 App bundle 或版本化的 Application Support runtime。
- [ ] 評估 `python-build-standalone` 作為可重定位的 arm64 Python；鎖定 wheels、授權清單與雜湊，不要求使用者安裝 Homebrew 或 Xcode。
- [ ] 由 App 監督 embedded daemon，並以 `SMAppService` 管理登入啟動；使用固定、可升級的 bundle 內路徑，不依賴 shell installer 或外部 LaunchAgent。
- [ ] 首次啟動加入 welcome flow：系統需求檢查、麥克風／輔助使用／輸入監控權限、熱鍵測試、模型準備進度與第一次試聽寫。
- [ ] 將大模型與 App 分離：首次使用時明示大小、儲存位置與下載進度，支援續傳、取消、重試、固定 revision、checksum 驗證和刪除模型。
- [ ] 將既有 `~/.open-dictate` 資料做成一次性、可回復的 migration，不遺失詞庫或設定。
- [ ] 加入 App 內 doctor、重啟服務、重設權限說明、匯出診斷資料與完整解除安裝。
- [ ] 產出可供測試的 `.dmg`；beta 階段仍可手動發布，但不得要求使用者執行 Terminal 指令。

**完成條件：** 在乾淨的 Apple Silicon Mac 上，只靠 Finder 與 App 內引導，在 10 分鐘內完成安裝及第一次聽寫；移除下載來源資料後仍可離線使用。

## Phase 2 — Signed and notarized public release

- [ ] 申請並設定 Developer ID Application 憑證與 hardened runtime。
- [ ] 補齊 entitlements；由內而外簽署 Python／MLX nested executables、dylibs、helper 與 App，不能以 `codesign --deep` 取代正確的簽署順序。
- [ ] 完成 Apple notarization、stapling，以及 `codesign --strict`、`spctl`、`stapler validate` 驗證。
- [ ] 發布簽章、公證過的 DMG；附 SHA-256 checksum、版本資訊、隱私說明與支援矩陣。
- [ ] 在乾淨 macOS VM／實機執行 Gatekeeper、安裝、權限、重開機、聽寫、doctor、uninstall 與 rollback 測試。
- [ ] CI release workflow 只能從受保護 tag 產生 artifact；公開安全掃描與 secret/history/blob 掃描必須先通過。

**完成條件：** 使用者雙擊 DMG、拖入 Applications 後可正常開啟，Gatekeeper 不顯示未識別開發者警告，重開機後服務能恢復。

## Phase 3 — Updates, rollback, and release operations

- [ ] 導入 Sparkle 2 或等價的簽章更新框架，使用 EdDSA appcast；更新 metadata 與 binary 分離簽章。
- [ ] runtime、模型與使用者資料分開版本化；App 更新不得覆蓋個人詞庫、紀錄或 speaker profile。
- [ ] 支援更新前健康檢查、失敗自動退回前一版、保留一個已知可用 runtime。
- [ ] 建立 Stable／Beta 更新頻道與版本支援政策；security／protocol 修正可要求立即更新。
- [ ] 每個 Open Dictate release 自動觸發 Muse Dictate overlay 相容性測試與版本升級提醒。

**完成條件：** 能從前一個正式版本在 App 內升級，保留所有使用者資料；刻意注入失敗更新時可自動退回且仍能聽寫。

## Phase 4 — Optional distribution channels

- [ ] 評估 Homebrew Cask，作為開發者與進階使用者的第二安裝管道。
- [ ] 等 sandbox、background service、模型下載與 Accessibility/Input Monitoring 權限限制確認可接受後，再評估 Mac App Store。
- [ ] 若增加 Intel 支援，必須獨立驗證 ASR backend、效能與 artifact；不能只做 universal binary 就宣稱支援。

## 建議的 artifact 邊界

```text
OpenDictate.app
├── Swift UI and onboarding
├── signed background service
├── private Python runtime and native libraries
├── daemon and deterministic lexicon engine
└── public starter glossary

~/Library/Application Support/OpenDictate/
├── models/
├── glossary/
├── review-queue/
├── speaker-profiles/
└── runtime-state/
```

模型預設不塞進 DMG：這能讓 App 下載較小、模型獨立升級，也讓使用者在首次啟動時清楚同意磁碟用量。若未來需要完全離線安裝，可另外提供含模型的大型 offline artifact，不與一般版本混在一起。

第一個正式安裝格式採 DMG，不採 PKG。現階段沒有需要 root 權限、system extension 或系統層安裝位置的元件；DMG 的安裝與移除較透明。若未來真的增加系統級元件，再重新評估 PKG。

## 暫不採用

- 不把現有 `install.sh` 直接包成 `.pkg`：它仍依賴開發工具與 clone 路徑，沒有解決可攜性。
- 不用 ad-hoc 簽章作公開發布：重新建置會破壞 TCC 權限身分，也無法提供正常 Gatekeeper 體驗。
- 不把模型、個人詞庫或 runtime data 永久寫進可覆蓋的 App bundle。
- 不先追求 Mac App Store；目前的全域熱鍵、Accessibility、Input Monitoring 與 background service 需要先做 sandbox 可行性驗證。

## 技術參考

- [Apple：發佈前公證 macOS 軟體](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
- [Apple：建立 macOS 發布簽章](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/)
- [Apple：SMAppService](https://developer.apple.com/documentation/servicemanagement/smappservice)
- [Apple：更新 App package installer 至新的 Service Management API](https://developer.apple.com/documentation/servicemanagement/updating-your-app-package-installer-to-use-the-new-service-management-api)
- [Sparkle：發布與更新簽章](https://sparkle-project.org/documentation/publishing/)
