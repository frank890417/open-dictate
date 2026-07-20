from __future__ import annotations
from pathlib import Path


def _srt_time(seconds) -> str:
    total_ms = max(0, int(round(float(seconds or 0) * 1000)))
    total, ms = divmod(total_ms, 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _subtitle_text(value) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def render_srt(segments: list[dict]) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        start = _srt_time(seg.get("start", 0))
        end = _srt_time(seg.get("end", seg.get("start", 0)))
        speaker = _subtitle_text(seg.get("speaker") or "SPEAKER_00")
        text = _subtitle_text(seg.get("text") or seg.get("raw") or "")
        blocks.append(f"{i}\n{start} --> {end}\n[{speaker}] {text}")
    return "\n\n".join(blocks) + "\n"


def write_srt(segments: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_srt(segments), encoding="utf-8")
