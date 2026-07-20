#!/usr/bin/env python3
"""Anonymous speaker labeling helpers."""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Iterable

try:
    from .base import SpeakerIdentifier, SpeakerMatch
except ImportError:
    from base import SpeakerIdentifier, SpeakerMatch  # type: ignore


class AnonymousSpeakerIdentifier(SpeakerIdentifier):
    """Always returns anonymous speaker identities."""

    def enroll(self, speaker_id: str, audio_path: Path, display_name: str | None = None) -> dict:
        raise RuntimeError("anonymous speaker mode does not enroll voice profiles")

    def identify(self, segment_audio: Path | bytes) -> SpeakerMatch:
        return SpeakerMatch("SPEAKER_00", None, 0.0, "anonymous")

    def list_profiles(self) -> list[dict]:
        return []


def normalize_speaker_labels(segments: Iterable[dict], source_key: str = "speaker") -> list[dict]:
    """Normalize arbitrary speaker labels to SPEAKER_00, SPEAKER_01, ..."""
    mapping: OrderedDict[str, str] = OrderedDict()
    out: list[dict] = []
    for seg in segments:
        raw = str(seg.get(source_key) or "SPEAKER")
        if raw not in mapping:
            mapping[raw] = f"SPEAKER_{len(mapping):02d}"
        row = dict(seg)
        row[source_key] = mapping[raw]
        out.append(row)
    return out
