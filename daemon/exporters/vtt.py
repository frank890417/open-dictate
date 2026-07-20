from __future__ import annotations
from pathlib import Path
from .srt import render_srt


def render_vtt(segments: list[dict]) -> str:
    lines = []
    for line in render_srt(segments).splitlines():
        if line.strip().isdigit():
            continue
        if " --> " in line:
            line = line.replace(",", ".")
        lines.append(line)
    return "WEBVTT\n\n" + "\n".join(lines).strip() + "\n"


def write_vtt(segments: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_vtt(segments), encoding="utf-8")
