#!/bin/bash
# open-dictate 一鍵安裝（雙模式）
#
#   ./install.sh               # standalone：自建 .venv-dictate + vendor starter glossaries
#   ./install.sh --developer    # developer：使用 OPEN_DICTATE_LEXICON_ROOT 指向外部相容詞庫 root
#
# 做的事：build .app → 乾淨安裝（rm 後 ditto，絕不 cp -R 蓋舊檔=簽章混血 SIGKILL 之雷）
# → 生成 launchd plist（路徑動態解析，不 hardcode 使用者）→ bootstrap 雙 job → ping 驗證。
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
MODE="standalone"
[ "${1:-}" = "--developer" ] && MODE="developer"

echo "▸ 模式：$MODE"

# -- 1. 解析 python / OPEN_DICTATE_LEXICON_ROOT ------------------------------------------
if [ "$MODE" = "standalone" ]; then
  OPEN_DICTATE_LEXICON_ROOT="$REPO/vendor"
  [ -f "$REPO/vendor/tools/muse-lexicon/muse_lexicon.py" ] || {
    echo "✗ vendor/ 不存在。分享者請先跑 scripts/sync-vendor.sh 再打包" >&2; exit 1; }
  PYVENV="$REPO/.venv-dictate"
  if [ ! -x "$PYVENV/bin/python3" ]; then
    echo "▸ 建 standalone venv + 安裝依賴（首次 ~1 分鐘）"
    python3 -m venv "$PYVENV"
    "$PYVENV/bin/pip" install -q -r daemon/requirements.txt
  fi
  PYTHON="$PYVENV/bin/python3"
elif [ "$MODE" = "developer" ]; then
  OPEN_DICTATE_LEXICON_ROOT="${OPEN_DICTATE_LEXICON_ROOT:-$REPO/vendor}"
  PYTHON="$OPEN_DICTATE_LEXICON_ROOT/tools/td-subtitle/.venv/bin/python3"
  [ -x "$PYTHON" ] || { echo "✗ 找不到 $PYTHON（developer 模式需要相容的外部詞庫 root）" >&2; exit 1; }
fi
"$PYTHON" -c "import mlx_whisper, opencc" || { echo "✗ 依賴驗證失敗（mlx_whisper/opencc）" >&2; exit 1; }
echo "  python: $PYTHON"
echo "  lexicon_root: $OPEN_DICTATE_LEXICON_ROOT"

# -- 2. build + 乾淨安裝 app ---------------------------------------------------
./build.sh
echo "▸ 乾淨安裝 /Applications/OpenDictate.app"
rm -rf /Applications/OpenDictate.app
ditto dist/OpenDictate.app /Applications/OpenDictate.app
codesign --verify --deep /Applications/OpenDictate.app

# -- 3. 生成 plist（動態路徑）--------------------------------------------------
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA" "$HOME/Library/Logs/open-dictate"

cat > "$LA/org.opendictate.daemon.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>org.opendictate.daemon</string>
	<key>ProgramArguments</key>
	<array>
		<string>${PYTHON}</string>
		<string>${REPO}/daemon/dictated.py</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict><key>OPEN_DICTATE_LEXICON_ROOT</key><string>${OPEN_DICTATE_LEXICON_ROOT}</string></dict>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><true/>
	<key>ProcessType</key><string>Interactive</string>
	<key>StandardOutPath</key><string>${HOME}/Library/Logs/open-dictate/daemon.out.log</string>
	<key>StandardErrorPath</key><string>${HOME}/Library/Logs/open-dictate/daemon.err.log</string>
</dict>
</plist>
PLIST

cat > "$LA/org.opendictate.shell.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>org.opendictate.shell</string>
	<key>ProgramArguments</key>
	<array><string>/Applications/OpenDictate.app/Contents/MacOS/OpenDictate</string></array>
	<key>EnvironmentVariables</key>
	<dict><key>OPEN_DICTATE_LEXICON_ROOT</key><string>${OPEN_DICTATE_LEXICON_ROOT}</string></dict>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><false/>
	<key>LimitLoadToSessionType</key><string>Aqua</string>
	<key>ProcessType</key><string>Interactive</string>
	<key>StandardOutPath</key><string>/tmp/open-dictate-shell.log</string>
	<key>StandardErrorPath</key><string>/tmp/open-dictate-shell.err.log</string>
</dict>
</plist>
PLIST

# -- 4. （重新）bootstrap 雙 job ------------------------------------------------
UID_N="$(id -u)"
launchctl bootout "gui/$UID_N/org.opendictate.daemon" 2>/dev/null || true
launchctl bootout "gui/$UID_N/org.opendictate.shell" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$UID_N" "$LA/org.opendictate.daemon.plist"
launchctl bootstrap "gui/$UID_N" "$LA/org.opendictate.shell.plist"

# -- 5. 驗證 -------------------------------------------------------------------
echo "▸ 等 daemon warm（模型載入 ~6s）…"
for i in $(seq 1 20); do [ -S /tmp/open-dictate.sock ] && break; sleep 1; done
/Applications/OpenDictate.app/Contents/MacOS/OpenDictate --probe-ping && echo "✓ daemon 通" || {
  echo "⚠️ daemon 未回應，看 log：~/Library/Logs/open-dictate/daemon.err.log" >&2; exit 1; }

echo
echo "✅ 安裝完成。首次使用："
echo "  1. 系統設定 > 隱私權與安全性 > 麥克風 + 輔助使用 → 勾 OpenDictate"
echo "     （重新 build 後簽章會變：已勾過的請取消再勾一次）"
echo "  2. 游標點進任何輸入框 → 按住 fn 講話 → 放開"
echo "  3. menu bar 🎙️ 圖示：設定 / 最近紀錄 / 回報誤聽（教詞庫）"
