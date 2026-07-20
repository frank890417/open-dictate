#!/bin/bash
# 移除 Open Dictate app 與 LaunchAgents；預設保留使用者資料。
set -euo pipefail

PURGE=0
if [ "${1:-}" = "--purge-data" ]; then
  PURGE=1
elif [ "$#" -gt 0 ]; then
  echo "用法：./uninstall.sh [--purge-data]" >&2
  exit 2
fi

UID_N="$(id -u)"
LA="$HOME/Library/LaunchAgents"
APP="/Applications/OpenDictate.app"
DATA="$HOME/.open-dictate"

echo "▸ 停止 Open Dictate LaunchAgents"
launchctl bootout "gui/$UID_N/org.opendictate.shell" 2>/dev/null || true
launchctl bootout "gui/$UID_N/org.opendictate.daemon" 2>/dev/null || true

echo "▸ 移除 app 與 LaunchAgent 設定"
/bin/rm -f "$LA/org.opendictate.shell.plist" "$LA/org.opendictate.daemon.plist"
/bin/rm -rf "$APP"

echo "✓ Open Dictate 已解除安裝"
echo "✓ 使用者資料仍保留：$DATA"
if [ "$PURGE" -eq 1 ]; then
  echo
  echo "基於安全考量，--purge-data 不會自動刪除資料。"
  echo "確認不再需要詞庫、逐字稿與紀錄後，請自行將以下資料夾移到垃圾桶："
  echo "  $DATA"
fi
