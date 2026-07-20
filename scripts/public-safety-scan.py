#!/usr/bin/env python3
"""Block obvious private data before publishing Open Dictate."""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCK_PATTERNS = [
    r"/Users/[A-Za-z0-9._-]+", r"BEGIN [A-Z ]*PRIVATE KEY", r"github_pat",
    r"ghp_[A-Za-z0-9]", r"gho_[A-Za-z0-9]",
    r"sk-[A-Za-z0-9]{20,}", r"xox[baprs]-", r"Bearer [A-Za-z0-9._-]{20,}",
    r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[\"']?[A-Za-z0-9._/-]{12,}",
]
BLOCK_FILES = [
    r"wispr", r"dictation-log", r"voiceprint", r"voiceprints/", r"speaker-profiles/",
    r"speaker_profiles.*\.json$", r"transcripts/", r"meetings/", r"review-queue/",
    r"\.wav$", r"\.m4a$", r"\.mp3$", r"\.aiff$", r"\.flac$", r"\.mp4$", r"\.mov$",
    r"\.npy$", r"\.pt$", r"\.pth$", r"\.pkl$", r"\.onnx$",
    r"\.sqlite$", r"\.db$", r"\.env",
]
SKIP_DIRS = {".git", ".build", "dist", ".venv", ".venv-dictate", "__pycache__"}
SKIP_FILES = {"scripts/public-safety-scan.py"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".sh", ".swift", ".plist", ".toml"}


def load_local_patterns() -> list[re.Pattern]:
    denylist = ROOT / ".public-safety-denylist.local"
    if not denylist.exists():
        return []
    patterns = []
    for line in denylist.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(re.compile(re.escape(line), re.I))
    return patterns


def skipped(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def main() -> int:
    hits: list[str] = []
    file_res = [re.compile(p, re.I) for p in BLOCK_FILES]
    text_res = [re.compile(p, re.I) for p in BLOCK_PATTERNS] + load_local_patterns()
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if skipped(path) or not path.is_file() or rel in SKIP_FILES:
            continue
        for rx in file_res:
            if rx.search(rel):
                hits.append(f"FILE {rel} matches {rx.pattern}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rx in text_res:
                if rx.search(line):
                    hits.append(f"TEXT {rel}:{i} matches {rx.pattern}: {line[:160]}")
    if hits:
        print("PUBLIC SAFETY SCAN FAILED")
        for hit in hits:
            print(hit)
        return 1
    print("PUBLIC SAFETY SCAN PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
