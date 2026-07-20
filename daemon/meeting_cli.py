#!/usr/bin/env python3
"""Open Dictate meeting transcription CLI.

Processes pre-transcribed JSON/JSONL segments or local audio files and exports a
reviewable meeting package. Audio ASR uses a local MLX Whisper backend; speaker
identity remains anonymous by default.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daemon.pipeline.audio_asr import is_audio_path  # type: ignore
from daemon.pipeline.meeting import run_from_audio, run_from_segments  # type: ignore

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
    trans = sub.add_parser("transcribe", help="process audio files or pre-transcribed JSON/JSONL segments")
    trans.add_argument("input", type=Path)
    trans.add_argument("--out", type=Path, required=True)
    trans.add_argument("--title", default="Open Dictate Meeting Transcript")
    trans.add_argument("--language", default="zh", help="ASR language, e.g. zh or auto")
    trans.add_argument("--model", default=None, help="MLX Whisper model repo for audio input")
    args = p.parse_args(argv)

    if args.cmd == "export-demo":
        source = args.out / "demo-input.json"
        _write_demo(source)
        result = run_from_segments(source, args.out, title="Open Dictate Demo Meeting")
    elif args.cmd == "transcribe":
        if is_audio_path(args.input):
            result = run_from_audio(args.input, args.out, title=args.title, language=args.language, model=args.model)
        else:
            result = run_from_segments(args.input, args.out, title=args.title, language=args.language)
    else:
        raise AssertionError(args.cmd)
    print(json.dumps({"ok": True, "exports": result.exports, "segments": len(result.segments)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
