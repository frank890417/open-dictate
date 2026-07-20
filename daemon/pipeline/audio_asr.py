#!/usr/bin/env python3
"""Local audio ASR adapter for Meeting Mode.

This module is intentionally thin and public-safe: audio stays local, mlx_whisper
is imported lazily, and the output is converted into the same segment contract as
pre-transcribed JSON/JSONL input.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LEXICON_ROOT = ROOT / "vendor" / "tools" / "muse-lexicon"
if str(LEXICON_ROOT) not in sys.path:
    sys.path.insert(0, str(LEXICON_ROOT))

from muse_lexicon import Lexicon  # type: ignore

DEFAULT_MODEL = os.environ.get("OPEN_DICTATE_MEETING_MODEL", "mlx-community/whisper-large-v3-turbo")
DEFAULT_LANGUAGE = os.environ.get("OPEN_DICTATE_MEETING_LANGUAGE", "zh")
SUPPORTED_AUDIO_SUFFIXES = {".wav", ".m4a", ".mp3", ".flac", ".aiff", ".aif"}

PROMPT_STYLE_SEED = "請以繁體中文與台灣慣用語轉錄，保留英文專名，例如 Open Dictate、TouchDesigner、Obsidian。"
PRIORITY_TERMS = "Open Dictate、OpenDictate、TouchDesigner、p5.js、Obsidian、Notion、Hermes、Python、Swift、macOS、Apple Silicon"


class AudioASRUnavailable(RuntimeError):
    """Raised when the local ASR backend cannot be loaded."""


def is_audio_path(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_AUDIO_SUFFIXES


def build_initial_prompt(lex: Lexicon) -> str:
    return PROMPT_STYLE_SEED + lex.build_initial_prompt(max_chars=180) + "、" + PRIORITY_TERMS


def _transcribe_backend(audio_path: Path, *, model: str, language: str, initial_prompt: str) -> dict[str, Any]:
    try:
        import mlx_whisper  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised through CLI behavior, not unit env
        raise AudioASRUnavailable(
            "mlx_whisper is not installed. Run ./install.sh or install daemon/requirements.txt."
        ) from exc

    kwargs: dict[str, Any] = {
        "path_or_hf_repo": model,
        "initial_prompt": initial_prompt,
    }
    # mlx-whisper supports `language="auto"` in recent versions; omitting the
    # argument would be ambiguous for tests, so keep the value explicit.
    if language:
        kwargs["language"] = language
    result = mlx_whisper.transcribe(str(audio_path), **kwargs)
    if not isinstance(result, dict):
        raise RuntimeError("mlx_whisper returned a non-dict result")
    return result


def segments_from_mlx_result(result: dict[str, Any]) -> list[dict]:
    segments = result.get("segments") or []
    out: list[dict] = []
    if isinstance(segments, list):
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "start": float(seg.get("start") or 0.0),
                "end": float(seg.get("end") or seg.get("start") or 0.0),
                "speaker": seg.get("speaker") or "SPEAKER",
                "raw": text,
                "asr_segment_id": seg.get("id", i),
            })
    if not out:
        text = str(result.get("text") or "").strip()
        if text:
            out.append({"start": 0.0, "end": 0.0, "speaker": "SPEAKER", "raw": text, "asr_segment_id": 0})
    return out


def transcribe_audio_segments(
    audio_path: Path,
    *,
    lex: Lexicon | None = None,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
) -> list[dict]:
    if not audio_path.exists() or not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    if not is_audio_path(audio_path):
        raise ValueError(f"unsupported audio file type: {audio_path.suffix}")
    if lex is None:
        lex = Lexicon.load(names=["general-zh", "muse-personal"])
    result = _transcribe_backend(
        audio_path,
        model=model,
        language=language,
        initial_prompt=build_initial_prompt(lex),
    )
    return segments_from_mlx_result(result)
