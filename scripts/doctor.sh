#!/bin/bash
# Open Dictate 唯讀診斷：不安裝、不啟動、不修改任何狀態。
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"
APP="/Applications/OpenDictate.app"
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

if [ -S /tmp/open-dictate.sock ]; then pass "socket 存在"; else fail "socket 不存在"; fi
if [ -x "$APP/Contents/MacOS/OpenDictate" ] && \
   "$APP/Contents/MacOS/OpenDictate" --probe-ping >/dev/null 2>&1; then
  pass "daemon ping 成功（模型 ready）"
else
  fail "daemon ping 失敗"
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
