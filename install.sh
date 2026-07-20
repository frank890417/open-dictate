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
case "${1:-}" in
  "") ;;
  --developer) MODE="developer" ;;
  -h|--help)
    echo "用法：./install.sh [--developer]"
    exit 0
    ;;
  *)
    echo "用法：./install.sh [--developer]" >&2
    exit 64
    ;;
esac
WARM_TIMEOUT_SECONDS="${OPEN_DICTATE_WARM_TIMEOUT:-180}"
case "$WARM_TIMEOUT_SECONDS" in
  ''|*[!0-9]*) echo "✗ OPEN_DICTATE_WARM_TIMEOUT 必須是正整數秒數" >&2; exit 64 ;;
esac
[ "$WARM_TIMEOUT_SECONDS" -gt 0 ] || { echo "✗ OPEN_DICTATE_WARM_TIMEOUT 必須大於 0" >&2; exit 64; }

echo "▸ 模式：$MODE"

# -- 0. preflight -------------------------------------------------------------
fail() { echo "✗ $*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || fail "缺少必要命令：$1"; }

echo "▸ 檢查安裝環境"
[ "$(uname -s)" = "Darwin" ] || fail "Open Dictate 目前只支援 macOS"
[ "$(uname -m)" = "arm64" ] || fail "需要 Apple Silicon Mac（目前架構：$(uname -m)）"
MACOS_MAJOR="$(sw_vers -productVersion | awk -F. '{print $1}')"
[ "$MACOS_MAJOR" -ge 14 ] || fail "需要 macOS 14 或更新版本（目前：$(sw_vers -productVersion)）"
xcode-select -p >/dev/null 2>&1 || fail "找不到 Xcode Command Line Tools。請先執行：xcode-select --install"
for cmd in python3 swift codesign ditto plutil launchctl; do need_cmd "$cmd"; done
[ -w /Applications ] || fail "目前帳號無法寫入 /Applications；請改用有安裝權限的 macOS 帳號"
python3 - <<'PY' || fail "需要 Python 3.11 或更新版本（目前：$(python3 --version 2>&1)）"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
echo "  macOS $(sw_vers -productVersion), $(uname -m)"
echo "  $(python3 --version 2>&1)"
echo "  $(swift --version | head -1)"

# -- 1. 解析 python / OPEN_DICTATE_LEXICON_ROOT ------------------------------------------
if [ "$MODE" = "standalone" ]; then
  OPEN_DICTATE_LEXICON_ROOT="$REPO/vendor"
  [ -f "$REPO/vendor/tools/muse-lexicon/muse_lexicon.py" ] || {
    echo "✗ vendor/ 不存在。分享者請先跑 scripts/sync-vendor.sh 再打包" >&2; exit 1; }
  PYVENV="$REPO/.venv-dictate"
  if [ ! -x "$PYVENV/bin/python3" ]; then
    echo "▸ 建 standalone venv + 安裝依賴（首次 ~1 分鐘）"
    python3 -m venv "$PYVENV"
    "$PYVENV/bin/python3" -m pip install --upgrade pip
  fi
  echo "▸ 對齊 Python 依賴"
  "$PYVENV/bin/python3" -m pip install -r daemon/requirements.txt
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
echo "▸ 等 daemon 載入模型（首次可能需要約 2 分鐘，最多等待 ${WARM_TIMEOUT_SECONDS} 秒）"
READY=0
i=1
while [ "$i" -le "$WARM_TIMEOUT_SECONDS" ]; do
  if [ -S /tmp/open-dictate.sock ] && \
     /Applications/OpenDictate.app/Contents/MacOS/OpenDictate --probe-ping >/dev/null 2>&1; then
    READY=1
    break
  fi
  if [ "$i" -eq 1 ] || [ $((i % 10)) -eq 0 ]; then
    echo "  還在準備模型… ${i}/${WARM_TIMEOUT_SECONDS} 秒"
  fi
  sleep 1
  i=$((i + 1))
done

if [ "$READY" -ne 1 ]; then
  echo "✗ daemon 在 ${WARM_TIMEOUT_SECONDS} 秒內未就緒" >&2
  echo "── launchd 狀態 ──" >&2
  launchctl print "gui/$UID_N/org.opendictate.daemon" 2>&1 | tail -40 >&2 || true
  echo "── daemon.err.log（最後 60 行）──" >&2
  tail -60 "$HOME/Library/Logs/open-dictate/daemon.err.log" 2>/dev/null >&2 || echo "（尚無 error log）" >&2
  echo "請執行 ./scripts/doctor.sh 取得完整診斷。" >&2
  exit 1
fi

echo "▸ 最終健康檢查"
/Applications/OpenDictate.app/Contents/MacOS/OpenDictate --probe-ping
echo "✓ daemon 已就緒；模型已載入。第一次實際聽寫仍需完成 macOS 權限設定。"

echo
echo "✅ 安裝完成。首次使用："
echo "  1. 系統設定 > 隱私權與安全性 → 為 OpenDictate 開啟："
echo "     • 麥克風"
echo "     • 輔助使用"
echo "     • 輸入監控"
echo "     （重新 build 後簽章會變：已勾過的請取消再勾一次）"
echo "  2. 若使用 fn：鍵盤設定中將『按下 fn/🌐 鍵』設為『不執行任何動作』"
echo "  3. 關閉其他使用 fn 的聽寫 app，避免熱鍵衝突"
echo "  4. 游標點進任何輸入框 → 按住 fn 講話 → 放開"
echo "  5. menu bar 🎙️ 圖示：設定 / 最近紀錄 / 回報誤聽（教詞庫）"
echo "  診斷：./scripts/doctor.sh"
