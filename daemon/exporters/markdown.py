from __future__ import annotations
from pathlib import Path
import html


def _fmt_time(seconds) -> str:
    if seconds is None:
        return "--:--"
    total = int(float(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _safe_inline(value) -> str:
    return html.escape(str(value or "").replace("\r", " ").replace("\n", " "), quote=False)


def render_markdown(segments: list[dict], title: str = "Open Dictate Meeting Transcript") -> str:
    lines = [f"# {_safe_inline(title)}", "", "> Generated locally by Open Dictate. Review QA flags before treating names, numbers, or technical terms as final.", ""]
    for seg in segments:
        speaker = _safe_inline(seg.get("speaker") or "SPEAKER_00")
        start = _fmt_time(seg.get("start"))
        text = _safe_inline(seg.get("text") or seg.get("raw") or "")
        lines.append(f"**{start} · {speaker}**  ")
        lines.append(text)
        flags = seg.get("qa_flags") or []
        if flags:
            lines.append("")
            for flag in flags:
                surface = _safe_inline(flag.get("surface"))
                candidate = _safe_inline(flag.get("candidate")) if flag.get("candidate") else ""
                reason = _safe_inline(flag.get("reason"))
                cand = f" → {candidate}" if candidate else ""
                lines.append(f"- ⚠ {surface}{cand}: {reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_markdown(segments: list[dict], path: Path, title: str = "Open Dictate Meeting Transcript") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(segments, title=title), encoding="utf-8")
