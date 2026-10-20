#!/bin/bash
# Open Dictate 唯讀診斷：不安裝、不啟動、不修改任何狀態。
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"
APP="${OPEN_DICTATE_APP_DIR:-/Applications}/OpenDictate.app"
SOCKET="${OPEN_DICTATE_SOCKET_PATH:-/tmp/open-dictate.sock}"
LA="$HOME/Library/LaunchAgents"
DAEMON_PLIST="$LA/org.opendictate.daemon.plist"
SHELL_PLIST="$LA/org.opendictate.shell.plist"
UID_N="$(id -u)"
FAILURES=0
WARNINGS=0

pass() { echo "✓ $*"; }
fail() { echo "✗ $*"; FAILURES=$((FAILURES + 1)); }
warn() { echo "⚠ $*"; WARNINGS=$((WARNINGS + 1)); }

echo "Open Dictate doctor（唯讀）"
echo "repo: $REPO"
echo

if [ -d "$APP" ]; then pass "app 已安裝：$APP"; else fail "app 不存在：$APP"; fi
if [ -d "$APP" ] && codesign --verify --deep "$APP" >/dev/null 2>&1; then
  pass "app codesign 驗證通過"
else
  fail "app codesign 驗證失敗或 app 不存在"
fi

for plist in "$DAEMON_PLIST" "$SHELL_PLIST"; do
  if [ ! -f "$plist" ]; then
    fail "缺少 LaunchAgent：$plist"
  elif plutil -lint "$plist" >/dev/null 2>&1; then
    pass "plist 有效：$(basename "$plist")"
  else
    fail "plist 格式無效：$plist"
  fi
done

if [ -f "$DAEMON_PLIST" ]; then
  if grep -Fq "$REPO/daemon/dictated.py" "$DAEMON_PLIST"; then
    pass "daemon plist 指向目前 clone"
  else
    warn "daemon plist 未指向目前 clone；移動/重抓 repo 後請重跑 ./install.sh"
  fi
  DAEMON_SCRIPT="$(sed -n 's:.*<string>\(.*daemon/dictated.py\)</string>.*:\1:p' "$DAEMON_PLIST" | head -1)"
  if [ -n "$DAEMON_SCRIPT" ] && [ -f "$DAEMON_SCRIPT" ]; then
    pass "plist 內 daemon 路徑存在"
  else
    fail "plist 內 daemon 路徑不存在（clone 可能已移動）"
  fi
fi

if launchctl print "gui/$UID_N/org.opendictate.daemon" >/dev/null 2>&1; then
  pass "daemon LaunchAgent 已載入"
else
  fail "daemon LaunchAgent 未載入"
fi
if launchctl print "gui/$UID_N/org.opendictate.shell" >/dev/null 2>&1; then
  pass "app LaunchAgent 已載入"
else
  warn "app LaunchAgent 未載入"
fi

if [ -S "$SOCKET" ]; then pass "socket 存在"; else fail "socket 不存在"; fi
if [ -x "$APP/Contents/MacOS/OpenDictate" ] && \
   "$APP/Contents/MacOS/OpenDictate" --probe-ping >/dev/null 2>&1; then
  pass "daemon ping 成功（模型 ready）"
else
  fail "daemon ping 失敗"
fi

# 辨識模式：只讀 plist 與 daemon 的 ping，不改任何東西
BACKEND=""
if [ -f "$DAEMON_PLIST" ]; then
  BACKEND="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:OPEN_DICTATE_ASR_BACKEND" "$DAEMON_PLIST" 2>/dev/null || true)"
fi
if [ "$BACKEND" = "router" ]; then
  WORKER="$(/usr/libexec/PlistBuddy -c "Print :EnvironmentVariables:OPEN_DICTATE_ASR_WORKER_PYTHON" "$DAEMON_PLIST" 2>/dev/null || true)"
  if [ -n "$WORKER" ] && [ -x "$WORKER" ]; then pass "辨識 worker 的 python 存在"; else fail "辨識 worker 的 python 不存在：${WORKER:-（plist 沒寫）}（重跑 ./install.sh）"; fi
  if python3 scripts/install-recognition.py needs-download --mode fast >/dev/null 2>&1; then
    warn "快／準模式要用的模型還沒完全下載到本機（約 4 GB），daemon 啟動時會卡在下載。請重跑 ./install.sh"
  else
    pass "快／準模式的模型都在本機"
  fi
  MODE_LINE="$(python3 daemon/dictate_cli.py --socket "$SOCKET" mode 2>&1 || true)"
  case "$MODE_LINE" in
    mode=fast*|mode=accurate*) pass "辨識模式：$MODE_LINE" ;;
    *) fail "daemon 沒有回報辨識模式：$MODE_LINE" ;;
  esac
else
  pass "辨識設定：單一 Whisper 模型，想開快／準模式請跑 ./install.sh --recognition fast"
fi

echo
echo "權限需在系統設定 > 隱私權與安全性中人工確認："
echo "  • 麥克風  • 輔助使用  • 輸入監控"
echo "doctor 不讀取或修改 macOS TCC 權限資料庫。"
echo
echo "結果：$FAILURES 個失敗，$WARNINGS 個警告"
if [ "$FAILURES" -gt 0 ]; then
  echo "log: $HOME/Library/Logs/open-dictate/daemon.err.log"
  exit 1
fi
exit 0
