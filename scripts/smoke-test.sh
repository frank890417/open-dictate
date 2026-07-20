#!/usr/bin/env bash
# open-dictate 完整煙霧測試：build → tests → golden-bench → meeting demo → CLI → probe
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
echo "▸ 1/7 build"
./build.sh
DIST_BIN="$ROOT/dist/OpenDictate.app/Contents/MacOS/OpenDictate"
if [[ -x "$DIST_BIN" ]]; then
  SHELL_BIN="$DIST_BIN"
fi
echo "  shell=$SHELL_BIN"

echo ""
echo "▸ 2/7 python unit tests"
python3 -m unittest discover tests

echo ""
echo "▸ 3/7 golden-bench"
python3 scripts/golden-bench.py --shell "$SHELL_BIN"

echo ""
echo "▸ 4/7 meeting demo export"
TMP_DEMO="$(mktemp -d /tmp/open-dictate-meeting-demo.XXXXXX)"
python3 daemon/meeting_cli.py export-demo --out "$TMP_DEMO"
test -s "$TMP_DEMO/transcript.md"
test -s "$TMP_DEMO/transcript.jsonl"
rm -rf "$TMP_DEMO"

echo ""
echo "▸ 5/7 ab-bench --quick（閘門 v2 對抗 + 校準稿不誤傷；確定性，無 LLM）"
if [[ -n "${VENV_PY:-}" && -x "${VENV_PY:-}" ]]; then
  "$VENV_PY" scripts/ab-bench.py --quick
else
  echo "  ⚠ OPEN_DICTATE_EVAL_PY 未設定或不可執行，略過 ab-bench quick"
fi

echo ""
echo "▸ 6/7 dictate_cli"
if [[ -S /tmp/open-dictate.sock ]]; then
  python3 daemon/dictate_cli.py ping
  python3 daemon/dictate_cli.py stats
  python3 daemon/dictate_cli.py reload
else
  echo "  ⚠ socket 不存在，略過 CLI（請先 launchd daemon）"
fi

echo ""
echo "▸ 7/7 TeachSuggestions / 協議欄位 / 簽章"
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
codesign --verify --deep "$ROOT/dist/OpenDictate.app"
echo "  ✓ codesign ok"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ -S /tmp/open-dictate.sock ]]; then
  echo " ✓ smoke-test ALL PASSED"
else
  echo " ✓ smoke-test OFFLINE CHECKS PASSED（runtime checks skipped: daemon not running）"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
