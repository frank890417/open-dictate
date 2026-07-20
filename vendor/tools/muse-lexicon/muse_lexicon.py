#!/usr/bin/env python3
"""muse-lexicon — 個人詞庫校正中央引擎 v0.1

契約 SSOT：~/Projects/open-dictate/IO-CONTRACT.md（compatible glossary root 副本：tools/open-dictate/IO-CONTRACT.md）
詞庫 SSOT：tools/td-subtitle/glossaries/*.json（correct-srt.py 生產在用，schema 向後相容鐵律）

校正哲學（project rule，不可違反）：
1. 確定性替換 only：詞庫 pair 命中才改。絕無 LLM rewrite、無語句重組、無格式化。
2. 寧可漏改，不可錯改：不確定 → 放過或 flag，絕不猜。
3. zh-TW 字形統一（OpenCC s2t + 後處理還原清單），修字形不是改句。
4. 數字絕不自動改。

API（見 IO-CONTRACT.md §muse_lexicon Python API）：
    from muse_lexicon import Lexicon
    lex = Lexicon.load(["general-zh", "muse-meeting", "muse-personal"])
    corrected, changes = lex.correct(text)
    prompt = lex.build_initial_prompt(max_chars=200)
    lex.add_pair(wrong, right, source="dictation")
    lex.flag(term, note, source="dictation")
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"

DEFAULT_GLOSSARIES = ["general-zh", "muse-meeting", "muse-personal"]
PERSONAL_GLOSSARY = "muse-personal"

# ---------------------------------------------------------------------------
# OpenCC（與 correct-srt.py 相同：s2t 字形轉換，僅修字形不動台灣口語用詞）
# ---------------------------------------------------------------------------
try:
    from opencc import OpenCC

    _cc = OpenCC("s2t")
    HAS_OPENCC = True
except ImportError:  # pragma: no cover - 環境相依
    _cc = None
    HAS_OPENCC = False

# Lambda registry（與 correct-srt.py 一致，供 general-zh 類物件清單 schema 的 callback）
LAMBDA_REGISTRY = {
    "sub_to_sop": lambda m: m.group().replace("Sub", "SOP"),
    "alt_to_out": lambda m: re.sub(r"Alt", "Out", m.group()),
}

# 行內 piece 迴圈（correct-srt.py Pattern 5 / speaker_id._DEHALL，實戰驗證門檻：
# 同 piece 重複 ≥6 次才收斂，正常強調如「加油加油加油」不受影響。寧可漏改不可錯改。）
INLINE_LOOP = re.compile(r"(.{1,12}?)([,，、 ]?\1){5,}")


def default_glossary_dir() -> Path:
    """glossary 目錄定位：相對本檔案 → env OPEN_DICTATE_LEXICON_ROOT fallback。不 hardcode 使用者名。"""
    here = Path(__file__).resolve().parent  # <compatible glossary root>/tools/muse-lexicon
    candidate = here.parent / "td-subtitle" / "glossaries"
    if candidate.is_dir():
        return candidate
    root = os.environ.get("OPEN_DICTATE_LEXICON_ROOT")
    if root:
        env_candidate = Path(root).expanduser() / "tools" / "td-subtitle" / "glossaries"
        if env_candidate.is_dir():
            return env_candidate
    return candidate  # 找不到也回傳預設路徑，load() 時逐檔警告


def apply_opencc(text: str) -> str:
    """簡→繁（台灣）字形轉換 + 過度轉換還原清單。逐字照搬 correct-srt.py apply_opencc。"""
    if not HAS_OPENCC:
        return text
    converted = _cc.convert(text)
    # s2t 過度轉換修正（台灣現代用法）
    converted = converted.replace("纔", "才")  # 纔→才（台灣用「才」）
    converted = converted.replace("裏", "裡")  # 裏→裡（台灣用「裡」）
    converted = converted.replace("臺", "台")  # 臺→台（台灣口語用「台」）
    converted = converted.replace("喫", "吃")  # 喫→吃（古字還原）
    converted = converted.replace("烏託邦", "烏托邦")  # 託→托（烏托邦是現代標準寫法）
    # 瞭→了：只在作為語助詞時還原（不影響「瞭解」）
    converted = re.sub(r"瞭(?!解|然|望|如指掌)", "了", converted)
    return converted


# ---------------------------------------------------------------------------
# 智慧全形標點（dictation 格式層，2026-07-11 project rule「原汁原味，但標點格式可以優化」）
# ---------------------------------------------------------------------------
# Whisper 中文輸出慣用半形標點（哈囉,哈囉）與 segment 縫隙空白（測試 系統）。
# 這層只做確定性「格式」修正：標點寬度 + CJK 縫隙空白。絕不動字、絕不加料、絕無 LLM。
# 防呆保留：3,000（千分位）、16:9（比例）、3.14（小數）、file.txt / U.S.（英文縮寫）、
# 省略號 ...、URL/英文句內標點（半形鄰居非中文即不動）。冪等：跑兩次結果相同。

_CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF),  # 漢字
    (0x3040, 0x30FF),                                        # 假名
)
_CJK_CONTEXT_EXTRA = "，。！？；：、「」『』（）《》〈〉…～"  # 全形標點也算中文語境
_HALF2FULL = {",": "，", "?": "？", "!": "！", ";": "；", ":": "："}
_FULL_OPENERS = "，。！？；：、「『（《〈"
_FULL_CLOSERS = "，。！？；：、」』）》〉"


def _is_cjk(ch: str) -> bool:
    cp = ord(ch) if ch else 0
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_cjk_context(ch: str) -> bool:
    # bool(ch) 防空字串：Python 的 "" in s 恆為 True（句首標點誤轉的雷）
    return _is_cjk(ch) or (bool(ch) and ch in _CJK_CONTEXT_EXTRA)


def smart_punct_zh(text: str) -> str:
    """中文語境半形標點 → 全形，並整理 CJK 縫隙空白。確定性、冪等。

    只在「前一個實字是中文語境」時轉換；英文句、數字、URL、檔名一律不動。
    中英交界空白保留（Traditional Chinese writing convention：跟 a collaborator 過），只併攏 CJK␣CJK。
    """
    if not text:
        return text
    chars = list(text)
    n = len(chars)

    def prev_solid(i: int) -> str:
        j = i - 1
        while j >= 0 and chars[j] == " ":
            j -= 1
        return chars[j] if j >= 0 else ""

    def next_solid(i: int) -> str:
        j = i + 1
        while j < n and chars[j] == " ":
            j += 1
        return chars[j] if j < n else ""

    for i, ch in enumerate(chars):
        if ch in _HALF2FULL:
            p, nx = prev_solid(i), next_solid(i)
            if p.isdigit() and nx.isdigit():
                continue  # 3,000 / 16:9 / 3:0
            # 前鄰是中文（哈囉,）或後鄰是中文（Review, 哈好／16比9, 今晚 — 校準 v1 抓到的縫）
            if _is_cjk_context(p) or _is_cjk_context(nx):
                chars[i] = _HALF2FULL[ch]
        elif ch == ".":
            # 省略號整組保留（whisper 常輸出 ...）
            if (i + 1 < n and chars[i + 1] == ".") or (i > 0 and chars[i - 1] == "."):
                continue
            p, nx = prev_solid(i), next_solid(i)
            if _is_cjk_context(p) and not (nx.isdigit() or (nx.isascii() and nx.isalpha())):
                chars[i] = "。"

    out = "".join(chars)

    # 空白整理：全形標點旁殘留空白移除；CJK␣CJK 併攏；中英交界保留
    result: list[str] = []
    for i, ch in enumerate(out):
        if ch == " ":
            left = result[-1] if result else ""
            right = out[i + 1] if i + 1 < len(out) else ""
            if left in _FULL_OPENERS and left:
                continue
            if right in _FULL_CLOSERS and right:
                continue
            if left and right and _is_cjk(left) and _is_cjk(right):
                continue
        result.append(ch)
    return "".join(result)


def _flatten_canonical(raw) -> list[str]:
    """_canonical 可能是 list（契約樣板）或 dict-of-lists（muse-personal 實檔：people/orgs/...）。"""
    terms: list[str] = []
    if isinstance(raw, dict):
        for key, values in raw.items():
            if key.startswith("_"):
                continue
            if isinstance(values, list):
                terms.extend(str(v) for v in values)
    elif isinstance(raw, list):
        terms.extend(str(v) for v in raw)
    return terms


def _extract_canonical(data: dict) -> list[str]:
    """收 top-level `_canonical` 與 `_meta._canonical` 兩種位置（實檔在 _meta 內）。"""
    terms: list[str] = []
    terms.extend(_flatten_canonical(data.get("_canonical")))
    meta = data.get("_meta")
    if isinstance(meta, dict):
        terms.extend(_flatten_canonical(meta.get("_canonical")))
    return terms


def _extract_flagged_terms(data: dict) -> list[str]:
    """_review_flagged：實檔是 dict（term → note，含 _note 說明鍵），契約樣板是 list。"""
    raw = data.get("_review_flagged")
    if isinstance(raw, dict):
        return [k for k in raw.keys() if not k.startswith("_")]
    if isinstance(raw, list):
        return [str(v) for v in raw]
    return []


def _diff_pairs(old: str, new: str) -> list[tuple[str, str]]:
    """(old, new) 差異片段對，用於 OpenCC 變更回報（僅記錄用，不影響輸出文字）。"""
    pairs: list[tuple[str, str]] = []
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            pairs.append((old[i1:i2], new[j1:j2]))
    return pairs


def _atomic_write_json(path: Path, data: dict) -> None:
    """temp file + os.replace 原子寫回，同目錄確保同 filesystem。"""
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class Lexicon:
    """中央詞庫引擎：載入多庫疊加、確定性校正、initial prompt、寫回個人詞庫。"""

    def __init__(self, names: list[str], glossary_dir: Path):
        self.names = list(names)
        self.glossary_dir = Path(glossary_dir)
        # (pattern, replacement_or_callable, desc)，依 names 順序疊加（同 correct-srt load_glossaries）
        self.replacements: list[tuple[str, object, str]] = []
        self.candidates: list[tuple[str, str, str]] = []  # phase2（不自動替換，僅供上層標記）
        self.canonical: list[str] = []
        self.flagged: list[str] = []
        # 語境 pair（2026-07-11）：誤聽的正字「本身是合法詞」（還好→Hahow、來彈→來談），
        # regex 盲換會誤殺 → 只提供給有語境判斷力的層（LLM punct+fix）搭配確定性白名單閘門用。
        # correct() 絕不使用這批。
        self.contextual: list[tuple[str, str]] = []
        self._load_all()

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, names: list[str] | None = None, glossary_dir=None) -> "Lexicon":
        if names is None:
            names = list(DEFAULT_GLOSSARIES)
        gdir = Path(glossary_dir) if glossary_dir else default_glossary_dir()
        return cls(names, gdir)

    def reload(self) -> None:
        """重讀所有詞庫（daemon 的 reload_lexicon、以及寫回後自我刷新用）。"""
        self.replacements = []
        self.candidates = []
        self.canonical = []
        self.flagged = []
        self.contextual = []
        self._load_all()

    def _load_all(self) -> None:
        for name in self.names:
            self._load_one(name.strip())

    def _load_one(self, name: str) -> None:
        path = self.glossary_dir / f"{name}.json"
        if not path.exists():
            print(f"⚠️ Glossary not found: {path}", file=sys.stderr)
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        raw = data.get("replacements", [])
        if isinstance(raw, dict):
            # 扁平 {誤聽: 正確} schema（muse-meeting / muse-personal）
            for pattern, repl in raw.items():
                self.replacements.append((pattern, repl, ""))
        else:
            # 物件清單 schema（general-zh：[{pattern, replacement, desc, lambda?}]）
            for r in raw:
                pattern = r["pattern"]
                if "lambda" in r and r["lambda"] in LAMBDA_REGISTRY:
                    repl = LAMBDA_REGISTRY[r["lambda"]]
                else:
                    repl = r["replacement"]
                self.replacements.append((pattern, repl, r.get("desc", "")))

        for c in data.get("phase2_candidates", []):
            self.candidates.append((c["pattern"], c.get("suggestion", "?"), c.get("desc", "")))

        self.canonical.extend(_extract_canonical(data))
        self.flagged.extend(_extract_flagged_terms(data))
        ctx = data.get("_contextual")
        if isinstance(ctx, dict):
            self.contextual.extend(
                (str(w), str(r)) for w, r in ctx.items()
                if not str(w).startswith("_") and str(w) != str(r)
            )

    # --------------------------------------------------------------- correct
    def correct(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """單句純文字確定性校正（非 SRT）。絕無 LLM、絕不動語序。

        ① glossary replacements（與 correct-srt.py apply_replacements 同語義）
        ② OpenCC s2t + 後處理還原清單
        ③ 行內 piece 迴圈收斂（≥6 次才動，寧可漏改）
        回傳 (corrected, changes)；changes 只記實際造成文字變化的 (old, new)。
        """
        changes: list[tuple[str, str]] = []
        new_text = text

        # ① 確定性替換（每 pattern 先收集 match 再由後往前替換，offset 不失效）
        for pattern, repl, _desc in self.replacements:
            matches = list(re.finditer(pattern, new_text))
            if not matches:
                continue
            if callable(repl):
                for m in reversed(matches):
                    old_match = m.group()
                    new_match = repl(m)
                    if old_match != new_match:
                        new_text = new_text[: m.start()] + new_match + new_text[m.end():]
                        changes.append((old_match, new_match))
            else:
                for m in reversed(matches):
                    old_match = m.group()
                    if old_match != repl:  # 恆等 pair（如 台灣→台灣）不列入 changes
                        changes.append((old_match, repl))
                    new_text = new_text[: m.start()] + repl + new_text[m.end():]

        # ② OpenCC 繁簡統一（修字形）
        converted = apply_opencc(new_text)
        if converted != new_text:
            changes.extend(_diff_pairs(new_text, converted))
            new_text = converted

        # ③ 行內迴圈收斂（correct-srt Pattern 5；lib 不做「整行移除」，殘渣去留交上層）
        collapsed = INLINE_LOOP.sub(r"\1", new_text).strip()
        if collapsed != new_text.strip():
            changes.append((new_text, collapsed))
            new_text = collapsed

        return new_text, changes

    # ---------------------------------------------------------------- prompt
    def _term_pool(self) -> list[str]:
        """去重詞池：三庫 _canonical 優先，再接 replacements 正確側值（跳過 lambda）。"""
        seen: set[str] = set()
        terms: list[str] = []

        def push(t) -> None:
            t = str(t).strip()
            if t and t not in seen:
                seen.add(t)
                terms.append(t)

        for t in self.canonical:
            push(t)
        for _pattern, repl, _desc in self.replacements:
            if callable(repl):
                continue
            push(repl)
        return terms

    def build_initial_prompt(self, max_chars: int = 200) -> str:
        """高頻專名串（逗號分隔）供 whisper initial_prompt；長度 ≤ max_chars。"""
        sep = ", "
        out: list[str] = []
        total = 0
        for t in self._term_pool():
            add = len(t) if not out else len(sep) + len(t)
            if total + add > max_chars:
                continue  # 單詞過長就跳過，繼續塞後面較短的（保持 canonical 優先序）
            out.append(t)
            total += add
        return sep.join(out)

    # -------------------------------------------------------------- write-back
    @property
    def personal_path(self) -> Path:
        return self.glossary_dir / f"{PERSONAL_GLOSSARY}.json"

    def _read_personal(self) -> dict:
        with open(self.personal_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _append_history(data: dict, action: str, wrong: str, right, source: str) -> None:
        """audit trail 進 _meta.history（新欄位只准 _ 前綴或 _meta 內 → history 在 _meta 內）。"""
        meta = data.setdefault("_meta", {})
        history = meta.setdefault("history", [])
        history.append(
            {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "action": action,
                "wrong": wrong,
                "right": right,
                "source": source,
            }
        )

    def add_pair(self, wrong: str, right: str, source: str = "manual") -> bool:
        """寫回 muse-personal.json 的 replacements（去重 + audit）。回傳是否實際寫入。"""
        wrong = str(wrong).strip()
        right = str(right).strip()
        if not wrong or not right:
            raise ValueError("add_pair: wrong/right 不可為空")
        if wrong == right:
            raise ValueError("add_pair: wrong == right（恆等 pair 請直接手改詞庫）")

        data = self._read_personal()
        reps = data.setdefault("replacements", {})
        if not isinstance(reps, dict):
            raise TypeError("muse-personal.json 的 replacements 應為 {誤聽: 正確} dict")

        if wrong in self.flagged:
            print(
                f"⚠️ add_pair: 「{wrong}」在 _review_flagged（曾判定有歧義），仍照指示入庫",
                file=sys.stderr,
            )

        if reps.get(wrong) == right:
            return False  # 已存在同 pair，去重
        action = "update" if wrong in reps else "add"
        reps[wrong] = right
        self._append_history(data, action=action, wrong=wrong, right=right, source=source)
        _atomic_write_json(self.personal_path, data)
        self.reload()
        return True

    def add_contextual(self, wrong: str, right: str, source: str = "manual") -> bool:
        """寫入 _contextual（語境 pair：regex 永不用、LLM 層搭白名單閘門用）。回傳是否實際寫入。"""
        wrong, right = str(wrong).strip(), str(right).strip()
        if not wrong or not right or wrong == right:
            raise ValueError("add_contextual: wrong/right 不可為空或相同")
        data = self._read_personal()
        ctx = data.setdefault("_contextual", {})
        if not isinstance(ctx, dict):
            raise TypeError("muse-personal.json 的 _contextual 應為 {誤聽: 正確} dict")
        if ctx.get(wrong) == right:
            return False
        ctx[wrong] = right
        self._append_history(data, "add-contextual", wrong, right, source)
        _atomic_write_json(self.personal_path, data)
        self.reload()
        return True

    def flag(self, term: str, note: str, source: str = "manual") -> bool:
        """歧義詞進 _review_flagged（不自動替換，人審後才入 replacements）。回傳是否實際寫入。"""
        term = str(term).strip()
        note = str(note).strip()
        if not term:
            raise ValueError("flag: term 不可為空")

        data = self._read_personal()
        flagged = data.setdefault("_review_flagged", {})

        if isinstance(flagged, dict):
            existing = flagged.get(term)
            if existing == note or (existing is not None and note and note in str(existing)):
                return False
            if existing:
                flagged[term] = f"{existing}；{note}" if note else existing
            else:
                flagged[term] = note
        elif isinstance(flagged, list):  # 契約樣板 list 變體：只存 term，note 留在 history
            if term in flagged:
                return False
            flagged.append(term)
        else:
            raise TypeError("muse-personal.json 的 _review_flagged 應為 dict 或 list")

        self._append_history(data, action="flag", wrong=term, right=note, source=source)
        _atomic_write_json(self.personal_path, data)
        self.reload()
        return True

    # ---------------------------------------------------------------- export
    def export_wispr(self) -> str:
        """Wispr 詞典匯出 stub：一行一詞的正確專名 txt（後續依 Wispr 實際格式細化）。"""
        return "\n".join(self._term_pool()) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="muse_lexicon.py",
        description=f"muse-lexicon v{__version__} — 個人詞庫校正中央引擎（確定性替換，絕無 LLM）",
    )
    parser.add_argument(
        "--glossary-dir", default=None, help="詞庫目錄（預設 <compatible glossary root>/tools/td-subtitle/glossaries）"
    )
    parser.add_argument(
        "--names",
        default=",".join(DEFAULT_GLOSSARIES),
        help=f"詞庫名稱逗號分隔（預設 {','.join(DEFAULT_GLOSSARIES)}）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_correct = sub.add_parser("correct", help="校正單句純文字")
    p_correct.add_argument("text", help="要校正的文字")
    p_correct.add_argument("--json", action="store_true", help="輸出 JSON {text, changes}")
    p_correct.add_argument("-v", "--verbose", action="store_true", help="stderr 列出 changes")

    p_punct = sub.add_parser("punct", help="智慧全形標點（格式層：中文語境半形→全形，不動字、無 LLM）")
    p_punct.add_argument("text")

    p_add = sub.add_parser("add", help="新增 誤聽→正確 pair 到 muse-personal.json（--contextual = 語境 pair，只給 LLM 層用）")
    p_add.add_argument("wrong", help="誤聽拼法")
    p_add.add_argument("right", help="唯一正確拼法")
    p_add.add_argument("--source", default="manual", help="來源（audit 用，預設 manual）")
    p_add.add_argument("--contextual", action="store_true",
                       help="語境 pair：誤聽的正字本身是合法詞（如 還好→Hahow），regex 永不套用，只給 LLM 層白名單")

    p_flag = sub.add_parser("flag", help="歧義詞進 _review_flagged（不自動替換）")
    p_flag.add_argument("term", help="歧義詞")
    p_flag.add_argument("--note", required=True, help="為何不能自動改（context 說明）")
    p_flag.add_argument("--source", default="manual", help="來源（audit 用，預設 manual）")

    p_prompt = sub.add_parser("prompt", help="輸出 whisper initial_prompt 專名串")
    p_prompt.add_argument("--max-chars", type=int, default=200)

    p_export = sub.add_parser("export", help="匯出詞庫（目前僅 wispr stub）")
    p_export.add_argument("--format", required=True, choices=["wispr"])
    p_export.add_argument("-o", "--output", default=None, help="輸出檔案（預設 stdout）")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    names = [n.strip() for n in args.names.split(",") if n.strip()]
    lex = Lexicon.load(names=names, glossary_dir=args.glossary_dir)

    if args.command == "punct":
        print(smart_punct_zh(args.text))
        return 0

    if args.command == "correct":
        corrected, changes = lex.correct(args.text)
        if args.json:
            print(json.dumps({"text": corrected, "changes": changes}, ensure_ascii=False))
        else:
            print(corrected)
        if args.verbose and changes:
            for old, new in changes:
                print(f"  {old} → {new}", file=sys.stderr)
        return 0

    if args.command == "add":
        if args.contextual:
            written = lex.add_contextual(args.wrong, args.right, source=args.source)
            kind = "語境 pair（LLM 層專用）"
        else:
            written = lex.add_pair(args.wrong, args.right, source=args.source)
            kind = ""
        if written:
            print(f"✅ 已入庫{kind}：{args.wrong} → {args.right}（source={args.source}）")
        else:
            print(f"⏭️ 已存在同 pair，未重複寫入：{args.wrong} → {args.right}")
        return 0

    if args.command == "flag":
        written = lex.flag(args.term, args.note, source=args.source)
        if written:
            print(f"🚩 已標記待審：{args.term}（{args.note}）")
        else:
            print(f"⏭️ 已在 _review_flagged，未重複寫入:{args.term}")
        return 0

    if args.command == "prompt":
        print(lex.build_initial_prompt(max_chars=args.max_chars))
        return 0

    if args.command == "export":
        content = lex.export_wispr()
        if args.output:
            Path(args.output).write_text(content, encoding="utf-8")
            print(f"✅ 匯出 {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(content)
        return 0

    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
