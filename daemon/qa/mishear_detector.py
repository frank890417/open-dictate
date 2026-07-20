#!/usr/bin/env python3
"""Post-transcription QA scanner.

This module flags possible mishearings; it does not mutate the glossary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEXICON_ROOT = ROOT / "vendor" / "tools" / "muse-lexicon"
if str(LEXICON_ROOT) not in sys.path:
    sys.path.insert(0, str(LEXICON_ROOT))

try:
    from muse_lexicon import Lexicon  # type: ignore
except Exception:  # pragma: no cover
    Lexicon = None  # type: ignore

NUMBERISH = re.compile(r"(?<![A-Za-z0-9])(?:\d{1,4}(?:[,，.]\d{3})*|\d+(?:\.\d+)?)(?:%|％|元|塊|萬|億|號|日|月|年)?")
MIXED_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
BUILTIN_CANONICAL = ["Obsidian", "Open Dictate", "OpenDictate", "TouchDesigner"]
PUBLIC_SAFE_HINTS = {
    "阿布西店": "Obsidian",
    "open dictate": "Open Dictate",
    "opendictate": "OpenDictate",
}


@dataclass
class QAFlag:
    type: str
    severity: str
    surface: str
    candidate: str | None
    reason: str
    start: float | None = None
    end: float | None = None
    speaker: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _load_canonical(glossary_dir: Path | None = None) -> list[str]:
    terms = set(BUILTIN_CANONICAL)
    if Lexicon is not None:
        try:
            lex = Lexicon.load(names=["general-zh", "muse-personal"], glossary_dir=glossary_dir)
            terms.update(str(t).strip() for t in getattr(lex, "canonical", []) if str(t).strip())
        except Exception:
            pass
    return sorted(terms, key=len, reverse=True)


def _candidate_terms(text: str) -> list[str]:
    terms: set[str] = set()
    terms.update(m.group(0) for m in MIXED_LATIN.finditer(text))
    for m in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9_.-]{3,18}", text):
        terms.add(m.group(0))
    return sorted(terms, key=len, reverse=True)


def scan_text(text: str, *, canonical: list[str] | None = None, start: float | None = None,
              end: float | None = None, speaker: str | None = None) -> list[QAFlag]:
    flags: list[QAFlag] = []
    seen_flags: set[tuple[str, str | None, str]] = set()
    canonical_terms = canonical if canonical is not None else _load_canonical()

    def add_flag(flag: QAFlag) -> None:
        key = (flag.type, flag.candidate, flag.surface)
        if key not in seen_flags:
            seen_flags.add(key)
            flags.append(flag)

    for m in NUMBERISH.finditer(text):
        add_flag(QAFlag(
            type="number_review",
            severity="medium",
            surface=m.group(0),
            candidate=None,
            reason="number/date/money-like span: never silently rewrite numeric meaning",
            start=start,
            end=end,
            speaker=speaker,
        ))

    lowered_text = text.lower()
    for surface, candidate in PUBLIC_SAFE_HINTS.items():
        if surface in text or surface in lowered_text:
            add_flag(QAFlag(
                type="possible_mishear",
                severity="medium",
                surface=surface,
                candidate=candidate,
                reason="matches public-safe review hint",
                start=start,
                end=end,
                speaker=speaker,
            ))

    for term in _candidate_terms(text):
        if term in canonical_terms:
            continue
        hinted = PUBLIC_SAFE_HINTS.get(term) or PUBLIC_SAFE_HINTS.get(term.lower())
        if hinted:
            add_flag(QAFlag(
                type="possible_mishear",
                severity="medium",
                surface=term,
                candidate=hinted,
                reason="matches public-safe review hint",
                start=start,
                end=end,
                speaker=speaker,
            ))
            continue
        best = None
        best_score = 0.0
        for canon in canonical_terms:
            if abs(len(canon) - len(term)) > max(8, len(canon)):
                continue
            score = difflib.SequenceMatcher(None, term.lower(), canon.lower()).ratio()
            if score > best_score:
                best_score = score
                best = canon
        if best and 0.45 <= best_score < 1.0:
            add_flag(QAFlag(
                type="possible_mishear",
                severity="medium" if best_score >= 0.68 else "low",
                surface=term,
                candidate=best,
                reason=f"near canonical term (similarity={best_score:.2f})",
                start=start,
                end=end,
                speaker=speaker,
            ))

    return flags


def scan_segments(segments: list[dict], *, glossary_dir: Path | None = None) -> list[dict]:
    canonical = _load_canonical(glossary_dir)
    out: list[dict] = []
    for seg in segments:
        text = str(seg.get("text") or seg.get("raw") or "")
        flags = scan_text(
            text,
            canonical=canonical,
            start=seg.get("start"),
            end=seg.get("end"),
            speaker=seg.get("speaker"),
        )
        row = dict(seg)
        row["qa_flags"] = [f.to_dict() for f in flags]
        out.append(row)
    return out


def load_segments(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("segments", []))
    return list(data)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Scan transcript JSON/JSONL for possible mishearings")
    p.add_argument("transcript", type=Path)
    p.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    args = p.parse_args(argv)
    rows = scan_segments(load_segments(args.transcript))
    flags = [flag for row in rows for flag in row.get("qa_flags", [])]
    if args.json:
        print(json.dumps({"flags": flags}, ensure_ascii=False, indent=2))
    else:
        print("# Open Dictate QA Report\n")
        if not flags:
            print("No review flags.")
        for flag in flags:
            loc = ""
            if flag.get("start") is not None:
                loc = f"{flag.get('start'):.2f}s"
            speaker = f" {flag.get('speaker')}" if flag.get("speaker") else ""
            cand = f" → {flag.get('candidate')}" if flag.get("candidate") else ""
            print(f"- {loc}{speaker} [{flag['severity']}] {flag['surface']}{cand}: {flag['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
