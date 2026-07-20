#!/usr/bin/env python3
"""Speaker identification interfaces for Open Dictate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SpeakerMatch:
    speaker_id: str
    display_name: str | None
    confidence: float
    status: str  # match | likely | unknown | anonymous


class SpeakerIdentifier:
    """Interface for speaker adapters.

    Public Open Dictate must work with anonymous labels by default. Any biometric
    speaker profile backend should store data locally and require explicit user
    enrollment.
    """

    def enroll(self, speaker_id: str, audio_path: Path, display_name: str | None = None) -> dict:
        raise NotImplementedError

    def identify(self, segment_audio: Path | bytes) -> SpeakerMatch:
        raise NotImplementedError

    def list_profiles(self) -> list[dict]:
        raise NotImplementedError
