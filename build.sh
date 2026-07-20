#!/bin/bash
# OpenDictate 打包：swift build -c release → 手工組 .app bundle → ad-hoc codesign
# 產出：dist/OpenDictate.app（安裝步驟見 docs/SETUP.md）
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="OpenDictate"
DIST_DIR="dist"
APP="$DIST_DIR/$APP_NAME.app"

echo "▸ swift build -c release"
swift build -c release --package-path OpenDictate

BIN="OpenDictate/.build/release/$APP_NAME"
[ -f "$BIN" ] || { echo "✗ 找不到 build 產物 $BIN" >&2; exit 1; }

echo "▸ 組 $APP"
rm -rf "$DIST_DIR"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/$APP_NAME"
cp packaging/Info.plist "$APP/Contents/Info.plist"
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
