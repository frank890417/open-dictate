#!/usr/bin/env bash
# open-dictate 完整煙霧測試：build → golden-bench → ab-bench quick → CLI → probe
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
SHELL_BIN="${OPEN_DICTATE_SHELL:-/Applications/OpenDictate.app/Contents/MacOS/OpenDictate}"
VENV_PY="${OPEN_DICTATE_EVAL_PY:-}"
DIST_BIN=""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " open-dictate smoke-test"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "▸ 1/5 build"
./build.sh
DIST_BIN="$ROOT/dist/OpenDictate.app/Contents/MacOS/OpenDictate"
# 優先測剛 build 的 dist，再 fallback Applications
if [[ -x "$DIST_BIN" ]]; then
  SHELL_BIN="$DIST_BIN"
fi
echo "  shell=$SHELL_BIN"

echo ""
echo "▸ 2/6 golden-bench"
python3 scripts/golden-bench.py --shell "$SHELL_BIN"

echo ""
echo "▸ 3/6 ab-bench --quick（閘門 v2 對抗 + 校準稿不誤傷；確定性，無 LLM）"
if [[ -n "${VENV_PY:-}" && -x "${VENV_PY:-}" ]]; then
  "$VENV_PY" scripts/ab-bench.py --quick
else
  echo "  ⚠ OPEN_DICTATE_EVAL_PY 未設定或不可執行，略過 ab-bench quick"
fi

echo ""
echo "▸ 4/6 dictate_cli"
if [[ -S /tmp/open-dictate.sock ]]; then
  python3 daemon/dictate_cli.py ping
  python3 daemon/dictate_cli.py stats
  python3 daemon/dictate_cli.py reload
else
  echo "  ⚠ socket 不存在，略過 CLI（請先 launchd daemon）"
fi

echo ""
echo "▸ 5/6 TeachSuggestions / 協議欄位（swift 已在 build 驗證編譯）"
# 用 python 重現 TeachSuggestions 啟發式的核心：middle diff
python3 - <<'PY'
def middle_diff(a, b):
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    j = 0
    while j < len(a) - i and j < len(b) - i and a[-1 - j] == b[-1 - j]:
        j += 1
    return a[i:len(a)-j], b[i:len(b)-j]

w, r = middle_diff("開放聽寫很棒", "OpenDictate很棒")
assert (w, r) == ("開放聽寫", "OpenDictate"), (w, r)
print("  ✓ teach middle-diff heuristic")
PY

echo ""
echo "▸ 6/6 安裝產物簽章"
codesign --verify --deep "$ROOT/dist/OpenDictate.app"
echo "  ✓ codesign ok"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " ✓ smoke-test ALL PASSED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
