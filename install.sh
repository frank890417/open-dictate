#!/bin/bash
# open-dictate 一鍵安裝
#
#   ./install.sh                          # 預設：快／準辨識模式都裝好，從「快」（Qwen3-ASR 1.7B）開始
#   ./install.sh --recognition accurate   # 同上，但從「準」（Whisper）開始
#   ./install.sh --recognition off        # 不開模式：只用一個 Whisper 模型（0.6.x 的行為）
#   ./install.sh --plan                   # 只說明這次會裝什麼、下載多少，什麼都不動
#   ./install.sh --developer              # 用 OPEN_DICTATE_LEXICON_ROOT 指向外部相容詞庫 root
#
# 做的事：（要下載模型時先說明大小與時間、等你同意）→ 建虛擬環境 → 下載模型 → build .app
# → 乾淨安裝（rm 後 ditto，絕不 cp -R 蓋舊檔=簽章混血 SIGKILL 之雷）→ 生成 launchd plist
# （路徑動態解析，不 hardcode 使用者）→ bootstrap 雙 job → ping 驗證，並確認辨識模式真的起來。
set -euo pipefail
cd "$(dirname "$0")"
REPO="$(pwd)"
MODE="standalone"
RECOG="fast"
RECOG_EXPLICIT=0
ASSUME_YES=0
PLAN_ONLY=0
APP_DIR="${OPEN_DICTATE_APP_DIR:-/Applications}"
SOCKET="${OPEN_DICTATE_SOCKET_PATH:-/tmp/open-dictate.sock}"

usage() {
  cat <<'USAGE'
用法： ./install.sh [選項]
  --recognition fast|accurate|off   辨識模式（預設 fast）
        fast       快：Qwen3-ASR 1.7B，自帶標點。快／準兩個模型都裝好，從「快」開始
        accurate   準：同上，從「準」（Whisper）開始
        off        不開模式：只用一個 Whisper 模型（0.6.x 的行為）
  --yes, -y      同意下載模型。要下載又不是互動終端機時一定要加
  --plan         只說明這次會裝什麼、下載多少，不安裝、不下載
  --developer    用 OPEN_DICTATE_LEXICON_ROOT 指向外部相容詞庫 root
環境變數：OPEN_DICTATE_WARM_TIMEOUT（等 daemon 就緒的秒數，預設 180）、
          OPEN_DICTATE_APP_DIR（app 安裝位置，預設 /Applications）、
          OPEN_DICTATE_SOCKET_PATH（預設 /tmp/open-dictate.sock）
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --developer) MODE="developer" ;;
    --recognition)
      [ "$#" -ge 2 ] || { usage >&2; exit 64; }
      RECOG="$2"; RECOG_EXPLICIT=1; shift ;;
    --recognition=*) RECOG="${1#*=}"; RECOG_EXPLICIT=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    --plan) PLAN_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 64 ;;
  esac
  shift
done
case "$RECOG" in
  fast|accurate|off) ;;
  *) echo "✗ --recognition 只接受 fast、accurate 或 off（收到：${RECOG}）" >&2; exit 64 ;;
esac
WARM_TIMEOUT_SECONDS="${OPEN_DICTATE_WARM_TIMEOUT:-180}"
case "$WARM_TIMEOUT_SECONDS" in
  ''|*[!0-9]*) echo "✗ OPEN_DICTATE_WARM_TIMEOUT 必須是正整數秒數" >&2; exit 64 ;;
esac
[ "$WARM_TIMEOUT_SECONDS" -gt 0 ] || { echo "✗ OPEN_DICTATE_WARM_TIMEOUT 必須大於 0" >&2; exit 64; }

if [ "$PLAN_ONLY" -eq 1 ]; then
  echo "▸ 安裝計畫（--plan：不會安裝或下載任何東西）"
  echo "  安裝方式：${MODE}　辨識：${RECOG}"
  python3 scripts/install-recognition.py plan --mode "$RECOG"
  echo "  會做的事：建 .venv-dictate$([ "$RECOG" != "off" ] && echo " 與 .venv-asr")，build app 裝到 $APP_DIR/OpenDictate.app、寫兩個 LaunchAgent。"
  echo "  你的資料 ~/.open-dictate 不會被動到。"
  exit 0
fi

echo "▸ 模式：${MODE}　辨識：${RECOG}"

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
mkdir -p "$APP_DIR" 2>/dev/null || true
[ -w "$APP_DIR" ] || fail "目前帳號無法寫入 ${APP_DIR}。請改用有安裝權限的 macOS 帳號，或設 OPEN_DICTATE_APP_DIR 指到可寫的資料夾"
python3 - <<'PY' || fail "需要 Python 3.11 或更新版本（目前：$(python3 --version 2>&1)）"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
echo "  macOS $(sw_vers -productVersion), $(uname -m)"
echo "  $(python3 --version 2>&1)"
echo "  $(swift --version | head -1)"

# -- 0b. 下載模型之前先說清楚大小與時間，等使用者同意 ---------------------------------
echo "▸ 辨識模型"
python3 scripts/install-recognition.py plan --mode "$RECOG"
if python3 scripts/install-recognition.py needs-download --mode "$RECOG"; then
  if [ "$ASSUME_YES" -ne 1 ]; then
    if [ -t 0 ]; then
      printf "要現在開始下載並繼續安裝嗎？[y/N] "
      read -r ANSWER || ANSWER=""
      case "$ANSWER" in
        y|Y|yes|YES) ;;
        *) echo "已取消：什麼都沒有安裝，也沒有下載。想用較小的設定，請跑 ./install.sh --recognition off" >&2; exit 1 ;;
      esac
    else
      echo "✗ 要下載模型，但這裡不是互動終端機，沒辦法問你。確認上面的大小與時間之後加 --yes 重跑，或改跑 ./install.sh --recognition off。" >&2
      exit 65
    fi
  fi
fi

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
  [ -x "$PYTHON" ] || { echo "✗ 找不到 ${PYTHON}（developer 模式需要相容的外部詞庫 root）" >&2; exit 1; }
fi
"$PYTHON" -c "import mlx_whisper, opencc" || { echo "✗ 依賴驗證失敗（mlx_whisper/opencc）" >&2; exit 1; }
echo "  python: $PYTHON"
echo "  lexicon_root: $OPEN_DICTATE_LEXICON_ROOT"

ASR_PYTHON=""
if [ "$RECOG" != "off" ]; then
  ASRVENV="$REPO/.venv-asr"
  if [ ! -x "$ASRVENV/bin/python3" ]; then
    echo "▸ 建辨識環境 .venv-asr（首次數分鐘）"
    python3 -m venv "$ASRVENV"
    "$ASRVENV/bin/python3" -m pip install --upgrade pip
  fi
  echo "▸ 對齊辨識環境的 Python 依賴"
  "$ASRVENV/bin/python3" -m pip install -r daemon/requirements-asr.txt
  "$ASRVENV/bin/python3" -c "import mlx_audio, mlx_whisper, soundfile" || { echo "✗ 辨識環境依賴驗證失敗（mlx_audio/mlx_whisper/soundfile）" >&2; exit 1; }
  ASR_PYTHON="$ASRVENV/bin/python3"
  echo "  asr python: $ASR_PYTHON"
fi

echo "▸ 下載辨識模型（已在本機的會略過）"
"${ASR_PYTHON:-$PYTHON}" scripts/install-recognition.py download --mode "$RECOG" || {
  echo "✗ 模型沒有下載完成，所以沒有安裝 app。重跑 ./install.sh 會繼續，已經下載完成的檔案不會重抓。" >&2; exit 1; }

# -- 2. build + 乾淨安裝 app ---------------------------------------------------
PRODUCT_SOCKET_PATH="$SOCKET" ./build.sh
echo "▸ 乾淨安裝 $APP_DIR/OpenDictate.app"
rm -rf "$APP_DIR/OpenDictate.app"
ditto dist/OpenDictate.app "$APP_DIR/OpenDictate.app"
codesign --verify --deep "$APP_DIR/OpenDictate.app"

# -- 3. 生成 plist（動態路徑）--------------------------------------------------
LA="$HOME/Library/LaunchAgents"
mkdir -p "$LA" "$HOME/Library/Logs/open-dictate"

# daemon 的環境變數：詞庫 root 一定有；辨識模式、socket、模型快取位置只在有設定時才寫
DAEMON_ENV="<key>OPEN_DICTATE_LEXICON_ROOT</key><string>${OPEN_DICTATE_LEXICON_ROOT}</string>"
[ "$SOCKET" = "/tmp/open-dictate.sock" ] || DAEMON_ENV="${DAEMON_ENV}<key>OPEN_DICTATE_SOCKET_PATH</key><string>${SOCKET}</string>"
[ -z "${HF_HOME:-}" ] || DAEMON_ENV="${DAEMON_ENV}<key>HF_HOME</key><string>${HF_HOME}</string>"
[ -z "${HF_HUB_CACHE:-}" ] || DAEMON_ENV="${DAEMON_ENV}<key>HF_HUB_CACHE</key><string>${HF_HUB_CACHE}</string>"
if [ "$RECOG" != "off" ]; then
  DAEMON_ENV="${DAEMON_ENV}<key>OPEN_DICTATE_ASR_BACKEND</key><string>router</string>"
  DAEMON_ENV="${DAEMON_ENV}<key>OPEN_DICTATE_ASR_WORKER_PYTHON</key><string>${ASR_PYTHON}</string>"
  DAEMON_ENV="${DAEMON_ENV}<key>OPEN_DICTATE_ASR_MODE</key><string>${RECOG}</string>"
fi

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
	<dict>${DAEMON_ENV}</dict>
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
	<array><string>${APP_DIR}/OpenDictate.app/Contents/MacOS/OpenDictate</string></array>
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

# 明確用 --recognition 指定了模式：把它記成「最後一次的選擇」（daemon 重啟後以這個為準）。
# 沒指定就不動，所以重跑 ./install.sh 不會洗掉你在選單列選過的模式。
if [ "$RECOG_EXPLICIT" -eq 1 ] && [ "$RECOG" != "off" ]; then
  python3 scripts/install-recognition.py state --mode "$RECOG" >/dev/null
fi

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
  if [ -S "$SOCKET" ] && \
     "$APP_DIR/OpenDictate.app/Contents/MacOS/OpenDictate" --probe-ping >/dev/null 2>&1; then
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
"$APP_DIR/OpenDictate.app/Contents/MacOS/OpenDictate" --probe-ping
if [ "$RECOG" != "off" ]; then
  MODE_LINE="$("$PYTHON" daemon/dictate_cli.py --socket "$SOCKET" mode 2>&1 || true)"
  echo "  辨識模式：$MODE_LINE"
  case "$MODE_LINE" in
    mode=fast*|mode=accurate*) ;;
    *) echo "✗ 辨識模式沒有起來（router 沒開）。看 ~/Library/Logs/open-dictate/daemon.err.log，或先用 ./install.sh --recognition off" >&2; exit 1 ;;
  esac
fi
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
if [ "$RECOG" = "off" ]; then
  echo "  6. 想開快／準辨識模式，請跑 ./install.sh（預設就是快），或 ./install.sh --recognition accurate"
else
  echo "  6. 辨識模式：選單列「辨識模式」切換快／準，或 python3 daemon/dictate_cli.py mode 快|準"
  echo "     想改用 Whisper 單模型，請跑 ./install.sh --recognition off"
fi
echo "  診斷：./scripts/doctor.sh"
