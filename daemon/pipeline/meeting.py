#!/usr/bin/env python3
"""Public-safe meeting transcript pipeline.

Meeting Mode accepts either pre-transcribed JSON/JSONL segments or local audio
files. Audio ASR is a thin MLX Whisper adapter behind the same segment contract;
speaker diarization remains an optional future layer, so public output defaults
to anonymous speaker labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path

from daemon.product_config import DATA_ROOT, LEXICON_ROOT as PRODUCT_LEXICON_ROOT, env

ROOT = Path(__file__).resolve().parents[2]
LEXICON_ROOT = PRODUCT_LEXICON_ROOT / "tools" / "muse-lexicon"
if str(LEXICON_ROOT) not in sys.path:
    sys.path.insert(0, str(LEXICON_ROOT))

from muse_lexicon import Lexicon  # type: ignore

LOCAL_PERSONAL_GLOSSARY = Path(env("PERSONAL_GLOSSARY", str(DATA_ROOT / "glossaries" / "personal.json"))).expanduser()

try:
    from .audio_asr import transcribe_audio_segments
    from ..qa.mishear_detector import scan_segments
    from ..speaker.anonymous import normalize_speaker_labels
    from ..exporters.jsonl import write_jsonl
    from ..exporters.markdown import write_markdown
    from ..exporters.srt import write_srt
    from ..exporters.vtt import write_vtt
except ImportError:
    from daemon.pipeline.audio_asr import transcribe_audio_segments  # type: ignore
    from daemon.qa.mishear_detector import scan_segments  # type: ignore
    from daemon.speaker.anonymous import normalize_speaker_labels  # type: ignore
    from daemon.exporters.jsonl import write_jsonl  # type: ignore
    from daemon.exporters.markdown import write_markdown  # type: ignore
    from daemon.exporters.srt import write_srt  # type: ignore
    from daemon.exporters.vtt import write_vtt  # type: ignore


@dataclass
class MeetingResult:
    meeting_id: str
    created_at: str
    language: str
    segments: list[dict]
    exports: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "meeting_id": self.meeting_id,
            "created_at": self.created_at,
            "language": self.language,
            "segments": self.segments,
            "exports": self.exports,
        }


def load_segments(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("segments", []))
    return list(data)


def _apply_local_personal(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Apply accepted local review-queue pairs from ~/.open-dictate, if present."""
    if not LOCAL_PERSONAL_GLOSSARY.exists():
        return text, []
    try:
        data = json.loads(LOCAL_PERSONAL_GLOSSARY.read_text(encoding="utf-8"))
        reps = data.get("replacements", {})
    except (OSError, json.JSONDecodeError):
        return text, []
    if not isinstance(reps, dict):
        return text, []
    out = text
    changes: list[tuple[str, str]] = []
    for wrong, right in reps.items():
        wrong, right = str(wrong), str(right)
        if wrong and right and wrong in out and wrong != right:
            out = out.replace(wrong, right)
            changes.append((wrong, right))
    return out, changes


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_segments(segments: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"segment {i} is not an object")
        row = dict(seg)
        start = _as_float(row.get("start"), 0.0)
        end = _as_float(row.get("end"), start)
        if start < 0:
            raise ValueError(f"segment {i} start is negative")
        if end < start:
            raise ValueError(f"segment {i} end is before start")
        row["start"] = start
        row["end"] = end
        clean.append(row)
    return clean


def correct_segments(segments: list[dict], lex: Lexicon | None = None) -> list[dict]:
    if lex is None:
        lex = Lexicon.load(names=["general-zh", "muse-personal"])
    out = []
    for seg in segments:
        row = dict(seg)
        raw = str(row.get("raw") or row.get("text") or "")
        text, changes = lex.correct(raw)
        text, local_changes = _apply_local_personal(text)
        row["raw"] = raw
        row["text"] = text
        row["changes"] = changes + local_changes
        out.append(row)
    return out


def process_segments(segments: list[dict]) -> list[dict]:
    corrected = correct_segments(validate_segments(segments))
    anonymous = normalize_speaker_labels(corrected)
    return scan_segments(anonymous)


def export_result(segments: list[dict], out_dir: Path, *, title: str = "Open Dictate Meeting Transcript") -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "jsonl": out_dir / "transcript.jsonl",
        "markdown": out_dir / "transcript.md",
        "srt": out_dir / "transcript.srt",
        "vtt": out_dir / "transcript.vtt",
    }
    write_jsonl(segments, paths["jsonl"])
    write_markdown(segments, paths["markdown"], title=title)
    write_srt(segments, paths["srt"])
    write_vtt(segments, paths["vtt"])
    return {k: str(v) for k, v in paths.items()}


def run_from_segments(path: Path, out_dir: Path, *, language: str = "zh-TW", title: str = "Open Dictate Meeting Transcript") -> MeetingResult:
    segments = process_segments(load_segments(path))
    meeting_id = path.stem
    exports = export_result(segments, out_dir, title=title)
    result = MeetingResult(
        meeting_id=meeting_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        language=language,
        segments=segments,
        exports=exports,
    )
    (out_dir / "meeting-result.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def run_from_audio(
    path: Path,
    out_dir: Path,
    *,
    language: str = "zh",
    title: str = "Open Dictate Meeting Transcript",
    model: str | None = None,
) -> MeetingResult:
    lex = Lexicon.load(names=["general-zh", "muse-personal"])
    kwargs = {"lex": lex, "language": language}
    if model:
        kwargs["model"] = model
    raw_segments = transcribe_audio_segments(path, **kwargs)
    segments = process_segments(raw_segments)
    meeting_id = path.stem
    exports = export_result(segments, out_dir, title=title)
    result = MeetingResult(
        meeting_id=meeting_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        language=language,
        segments=segments,
        exports=exports,
    )
    (out_dir / "meeting-result.json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
