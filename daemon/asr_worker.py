#!/usr/bin/env python3
"""Local recognition subprocess for dictated (ASR_BACKEND=router).

The daemon talks to it over stdin/stdout, one JSON object per line. Running MLX in a
child process means a native crash (an MLX segfault such as the `compile_dfs` recursion
seen on 2026-10-16) kills only this process: the daemon restarts it and sends the current
utterance down the other path instead of dropping it.

Protocol:
  worker → {"ready": true, "models": {...load seconds...}, "engine": "1.0.0", "pid": 123}
  daemon → {"id": 7, "model": "qwen3-asr-1.7b", "f32": "/path/clip.f32", "prompt": null, "hotwords": [...]}
           ("f32" = raw float32 mono 16 kHz samples; "wav" = a file path decoded by the library)
  worker → {"id": 7, "ok": true, "text": "...", "segments": [...], "engine_ms": 41, "model": "..."}
           {"id": 7, "ok": false, "error": "..."}
stdin EOF (daemon gone) → exit.

Usage: python asr_worker.py --models qwen3-asr-1.7b[,whisper-turbo]
"""

from __future__ import annotations

import json
import os
import sys

# Keep the protocol channel clean: libraries print progress bars and notices to stdout.
_PROTO = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
os.dup2(2, 1)
sys.stdout = sys.stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402

import asr_engine as E  # noqa: E402


def send(obj: dict) -> None:
    _PROTO.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _PROTO.flush()


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    args = ap.parse_args()
    keys = [E.resolve(m) for m in args.models.split(",") if m.strip()]
    engine = E.Engine()
    for k in keys:
        engine.load(k)
    send({"ready": True, "models": engine.loaded, "engine": E.ENGINE_VERSION, "pid": os.getpid()})
    for line in sys.stdin:
        if not line.strip():
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            if req.get("cmd") == "ping":
                send({"id": rid, "ok": True})
                continue
            if req.get("f32"):
                audio = np.fromfile(req["f32"], dtype=np.float32)
            else:
                audio = req["wav"]
            res = engine.transcribe(req.get("model") or keys[0], audio, req.get("prompt"), req.get("hotwords"))
            send({"id": rid, "ok": True, **res})
        except Exception as e:  # noqa: BLE001 — one bad request must not kill the worker
            send({"id": rid, "ok": False, "error": f"{e.__class__.__name__}: {e}"[:300]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
