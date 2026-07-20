#!/bin/bash
# vendor 同步：把 compatible glossary root 的 muse_lexicon.py + 詞庫 schema 快照進 vendor/（分享用 standalone 模式）
# vendor/ 的結構 = 迷你 compatible glossary root root（daemon 用 OPEN_DICTATE_LEXICON_ROOT=vendor/ 即可，零程式改動）：
#   vendor/tools/muse-lexicon/muse_lexicon.py     ← 快照（單向：compatible glossary root → 這裡；別反向改）
#   vendor/tools/td-subtitle/glossaries/*.json    ← starter 詞庫（general-zh 快照 + 空 personal）
# 每次 muse_lexicon.py 有重要更新後重跑本腳本。快照 hash 印在 vendor/VENDOR-STAMP.txt。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
cd "$(dirname "$0")/.."

LEXICON_ROOT="${OPEN_DICTATE_LEXICON_ROOT:-${REPO}/vendor}"
SRC_LEX="$LEXICON_ROOT/tools/muse-lexicon/muse_lexicon.py"
SRC_GLOSS="$LEXICON_ROOT/tools/td-subtitle/glossaries"
[ -f "$SRC_LEX" ] || { echo "✗ 找不到 $SRC_LEX（設 OPEN_DICTATE_LEXICON_ROOT 或先 clone compatible glossary root）" >&2; exit 1; }

mkdir -p vendor/tools/muse-lexicon vendor/tools/td-subtitle/glossaries
DEST_LEX="$REPO/vendor/tools/muse-lexicon/muse_lexicon.py"
if [ "$(cd "$(dirname "$SRC_LEX")" && pwd)/$(basename "$SRC_LEX")" != "$DEST_LEX" ]; then
  cp "$SRC_LEX" "$DEST_LEX"
fi

# starter 詞庫：general-zh（通用層，無個資）照搬；personal 給空殼 schema（分享對象自己長）
if [ -f "$SRC_GLOSS/general-zh.json" ]; then
  SRC_GENERAL="$(cd "$SRC_GLOSS" && pwd)/general-zh.json"
  DEST_GENERAL="$REPO/vendor/tools/td-subtitle/glossaries/general-zh.json"
  [ "$SRC_GENERAL" = "$DEST_GENERAL" ] || cp "$SRC_GENERAL" "$DEST_GENERAL"
fi
cat > vendor/tools/td-subtitle/glossaries/muse-personal.json <<'JSON'
{
  "_meta": {"note": "個人詞庫（starter 空殼）：用 menu bar「回報誤聽」或 muse_lexicon.py add 逐漸長大", "history": [], "_canonical": {}},
  "replacements": {},
  "_review_flagged": {}
}
JSON

# ⚠️ 絕不搬 muse-meeting.json / voiceprints（含user會議專名與他人資料——隱私邊界）
STAMP="vendor/VENDOR-STAMP.txt"
{
  echo "synced: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "muse_lexicon.py sha256: $(shasum -a 256 vendor/tools/muse-lexicon/muse_lexicon.py | cut -d' ' -f1)"
  echo "source: $SRC_LEX"
} > "$STAMP"
echo "✓ vendor 同步完成"; cat "$STAMP"
