#!/usr/bin/env python3
"""Open Dictate meeting transcription CLI.

Current public MVP processes pre-transcribed JSON/JSONL segments and exports a
reviewable meeting package. Real audio ASR is intentionally not faked: audio
input exits with an explicit message until an ASR backend is wired in.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daemon.pipeline.meeting import run_from_segments  # type: ignore

DEMO_SEGMENTS = [
    {"start": 0.0, "end": 3.2, "speaker": "alice", "raw": "今天我們測試open dictate的會議模式"},
    {"start": 3.2, "end": 7.8, "speaker": "bob", "raw": "如果聽錯阿布西店就要進入審核而不是直接改"},
    {"start": 7.8, "end": 11.0, "speaker": "alice", "raw": "預算是3000元這種數字要標記確認"},
]


def _write_demo(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"segments": DEMO_SEGMENTS}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Open Dictate meeting transcript pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("export-demo", help="write and process a fictional public-safe demo transcript")
    demo.add_argument("--out", type=Path, required=True)
    trans = sub.add_parser("transcribe", help="process pre-transcribed JSON/JSONL segments")
    trans.add_argument("input", type=Path)
    trans.add_argument("--out", type=Path, required=True)
    trans.add_argument("--title", default="Open Dictate Meeting Transcript")
    args = p.parse_args(argv)

    if args.cmd == "export-demo":
        source = args.out / "demo-input.json"
        _write_demo(source)
        result = run_from_segments(source, args.out, title="Open Dictate Demo Meeting")
    elif args.cmd == "transcribe":
        if args.input.suffix.lower() in {".wav", ".m4a", ".mp3", ".flac", ".aiff"}:
            print("Audio ASR backend is not enabled in this public MVP. Provide JSON/JSONL segments or wire a local ASR adapter.", file=sys.stderr)
            return 2
        result = run_from_segments(args.input, args.out, title=args.title)
    else:
        raise AssertionError(args.cmd)
    print(json.dumps({"ok": True, "exports": result.exports, "segments": len(result.segments)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
