#!/bin/bash
# OpenDictate 打包：swift build -c release → 手工組 .app bundle → ad-hoc codesign
# 產出：dist/OpenDictate.app（安裝步驟見 docs/SETUP.md）
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="${PRODUCT_APP_NAME:-OpenDictate}"
EXECUTABLE="${PRODUCT_EXECUTABLE:-$APP_NAME}"
PRODUCT_ID="${PRODUCT_ID:-open-dictate}"
BUNDLE_ID="${PRODUCT_BUNDLE_ID:-org.opendictate.shell}"
SOCKET_PATH="${PRODUCT_SOCKET_PATH:-/tmp/open-dictate.sock}"
DATA_ROOT="${PRODUCT_DATA_ROOT:-~/.open-dictate}"
LOG_ROOT="${PRODUCT_LOG_ROOT:-${DATA_ROOT}/dictation-log}"
DAEMON_LABEL="${PRODUCT_DAEMON_LABEL:-org.opendictate.daemon}"
SHELL_LABEL="${PRODUCT_SHELL_LABEL:-${BUNDLE_ID}}"
ENV_PREFIX="${PRODUCT_ENV_PREFIX:-OPEN_DICTATE}"
PRIORITY_TERMS="${PRODUCT_PRIORITY_TERMS:-Open Dictate、OpenDictate、TouchDesigner、p5.js、Obsidian、Notion、Hermes、Python、Swift、macOS、Apple Silicon}"
SWIFT_PRODUCT="OpenDictate"
DIST_DIR="dist"
APP="$DIST_DIR/$APP_NAME.app"

echo "▸ swift build -c release"
swift build -c release --package-path OpenDictate

BIN="OpenDictate/.build/release/$SWIFT_PRODUCT"
[ -f "$BIN" ] || { echo "✗ 找不到 build 產物 $BIN" >&2; exit 1; }

echo "▸ 組 $APP"
rm -rf "$DIST_DIR"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$EXECUTABLE"
cp packaging/Info.plist "$APP/Contents/Info.plist"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName $APP_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $APP_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleExecutable $EXECUTABLE" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription $APP_NAME 需要麥克風錄下你按住熱鍵時說的話。轉錄全程在本機進行，語音不出境。" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODProductID $PRODUCT_ID" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODSocketPath $SOCKET_PATH" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODDataRoot $DATA_ROOT" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODLogRoot $LOG_ROOT" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODDaemonLaunchLabel $DAEMON_LABEL" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODShellLaunchLabel $SHELL_LABEL" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODEnvironmentPrefix $ENV_PREFIX" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :ODPriorityTerms $PRIORITY_TERMS" "$PLIST"
# Keep the standalone fallback usable after the source checkout is removed.
ditto vendor "$APP/Contents/Resources/vendor"
printf 'APPL????' > "$APP/Contents/PkgInfo"

plutil -lint "$APP/Contents/Info.plist" >/dev/null

SIGN_ID="OpenDictate Local Signing"
if security find-identity -v -p codesigning 2>/dev/null | grep -q "$SIGN_ID"; then
  echo "▸ codesign（穩定本機憑證：$SIGN_ID — 簽章跨 build 不變，TCC 授權存活）"
  codesign --force --deep --sign "$SIGN_ID" "$APP"
else
  echo "▸ ad-hoc codesign（找不到本機憑證 — 每次重 build 需重授權）"
  codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep "$APP"

echo
echo "✓ 完成：$APP"
echo
echo "下一步（首次安裝，詳見 docs/SETUP.md）："
echo "  ./install.sh"
echo "  # install.sh 會安裝 app、生成 LaunchAgent plist，並啟動 daemon/app"
echo
echo "⚠️ ad-hoc 簽章：重新 build 後簽章會變，系統可能要求重新授權"
echo "   （隱私權與安全性 > 輔助使用/輸入監控 取消再勾選）。"
