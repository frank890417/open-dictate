#!/usr/bin/env python3
"""Review-first glossary growth for Open Dictate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

DEFAULT_STATE_DIR = Path("~/.open-dictate/review-queue").expanduser()
DEFAULT_QUEUE = DEFAULT_STATE_DIR / "candidates.json"
DEFAULT_GLOSSARY = Path("~/.open-dictate/glossaries/personal.json").expanduser()


@dataclass
class Candidate:
    id: str
    wrong: str
    candidate: str
    source: str
    status: str = "pending"
    reason: str = ""
    count: int = 1
    created_at: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if not d["created_at"]:
            d["created_at"] = datetime.now(timezone.utc).isoformat()
        return d


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


class ReviewQueue:
    def __init__(self, queue_path: Path = DEFAULT_QUEUE, glossary_path: Path = DEFAULT_GLOSSARY):
        self.queue_path = Path(queue_path).expanduser()
        self.glossary_path = Path(glossary_path).expanduser()

    def list(self, status: str | None = None) -> list[dict]:
        rows = _read_json(self.queue_path, {"candidates": []}).get("candidates", [])
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    def add(self, wrong: str, candidate: str, *, source: str = "qa", reason: str = "") -> dict:
        wrong = wrong.strip()
        candidate = candidate.strip()
        if not wrong or not candidate:
            raise ValueError("wrong/candidate cannot be empty")
        data = _read_json(self.queue_path, {"candidates": []})
        for row in data["candidates"]:
            if row.get("wrong") == wrong and row.get("candidate") == candidate and row.get("status") == "pending":
                row["count"] = int(row.get("count", 1)) + 1
                _write_json(self.queue_path, data)
                return row
        row = Candidate(id=f"cand_{uuid4().hex[:10]}", wrong=wrong, candidate=candidate, source=source, reason=reason).to_dict()
        data["candidates"].append(row)
        _write_json(self.queue_path, data)
        return row

    def _find(self, candidate_id: str) -> tuple[dict, dict]:
        data = _read_json(self.queue_path, {"candidates": []})
        for row in data["candidates"]:
            if row.get("id") == candidate_id:
                return data, row
        raise KeyError(f"candidate not found: {candidate_id}")

    def accept(self, candidate_id: str, *, right: str | None = None) -> dict:
        data, row = self._find(candidate_id)
        if row.get("status") != "pending":
            raise ValueError(f"candidate is not pending: {candidate_id}")
        wrong = row["wrong"]
        final = (right or row["candidate"]).strip()
        glossary = _read_json(self.glossary_path, {"_meta": {"version": "0.2.0", "privacy": "local-only"}, "replacements": {}, "_history": []})
        reps = glossary.setdefault("replacements", {})
        reps[wrong] = final
        glossary.setdefault("_history", []).append({
            "date": datetime.now(timezone.utc).isoformat(),
            "action": "accept",
            "wrong": wrong,
            "right": final,
            "source": row.get("source", "review-queue"),
            "candidate_id": candidate_id,
        })
        row["status"] = "accepted"
        row["accepted_as"] = final
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(self.glossary_path, glossary)
        _write_json(self.queue_path, data)
        return row

    def reject(self, candidate_id: str, *, reason: str = "") -> dict:
        data, row = self._find(candidate_id)
        if row.get("status") != "pending":
            raise ValueError(f"candidate is not pending: {candidate_id}")
        row["status"] = "rejected"
        row["reject_reason"] = reason
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(self.queue_path, data)
        return row

    def undo(self, candidate_id: str) -> dict:
        data, row = self._find(candidate_id)
        if row.get("status") not in {"accepted", "rejected"}:
            raise ValueError(f"candidate is not accepted/rejected: {candidate_id}")
        if row.get("status") == "accepted":
            glossary = _read_json(self.glossary_path, {"replacements": {}, "_history": []})
            wrong = row.get("wrong")
            if glossary.get("replacements", {}).get(wrong) == row.get("accepted_as"):
                del glossary["replacements"][wrong]
            glossary.setdefault("_history", []).append({
                "date": datetime.now(timezone.utc).isoformat(),
                "action": "undo",
                "candidate_id": candidate_id,
            })
            _write_json(self.glossary_path, glossary)
        row["status"] = "pending"
        row.pop("accepted_as", None)
        row.pop("reject_reason", None)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(self.queue_path, data)
        return row
