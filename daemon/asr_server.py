#!/usr/bin/env python3
"""Remote recognition service for dictated (runs on another Mac on the same private network).

The daemon on the laptop sends each utterance here first and falls back to its own local
worker (same asr_engine, same model) on timeout or when this host is unreachable.

- Binds one address only (use the tailnet / private address; never 0.0.0.0).
- Shared token, read from the macOS keychain at start (``--token-keychain SERVICE``,
  account ``token``) or from an environment variable; never from a file.
- One inference thread owns MLX; requests queue by priority (main before shadow) and are
  dropped when their client-side deadline has already passed.
- Accepts FLAC (``audio/flac``) or 16 kHz PCM16 WAV.
- Logs timings only. Audio and transcripts are never written to disk or to the log.

Endpoints:
  GET  /health                      → {"ok", "version", "engine", "models", "busy", "queue", ...}  (no token)
  POST /v1/transcribe?model=KEY&priority=main|shadow
       headers: X-Token, Content-Type, X-Deadline-Ms (optional), X-ASR-Prompt (optional, base64url UTF-8),
                X-ASR-Hotwords (optional, base64url UTF-8 JSON list)
       → {"ok": true, "text", "segments", "model", "engine_ms", "queue_ms", "server_ms"}

Usage:
  python asr_server.py --bind 100.x.y.z --port 8795 --models qwen3-asr-1.7b,whisper-turbo \
      --token-keychain open-dictate-asr
"""

from __future__ import annotations

import argparse
import base64
import hmac
import itertools
import json
import os
import queue
import resource
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asr_engine as E  # noqa: E402

VERSION = "1.1.0"
MAX_BODY = 32 << 20          # ~17 minutes of 16 kHz PCM16; FLAC is about half
PRIORITY = {"main": 0, "shadow": 1}


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def keychain_secret(service: str, account: str = "token") -> str:
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"keychain item {service}/{account} not readable (rc={r.returncode}): {r.stderr.strip()}")
    return r.stdout.strip()


class Job:
    __slots__ = ("model", "audio", "prompt", "hotwords", "deadline", "enq", "done", "result", "error", "started")

    def __init__(self, model, audio, prompt, hotwords, deadline):
        self.model, self.audio, self.prompt, self.hotwords, self.deadline = model, audio, prompt, hotwords, deadline
        self.enq = time.monotonic()
        self.done = threading.Event()
        self.result = self.error = self.started = None


class Inference(threading.Thread):
    """The only thread that touches MLX: loads the models, then serves the priority queue."""

    def __init__(self, models: list[str]) -> None:
        super().__init__(name="inference", daemon=True)
        self.models = models
        self.engine = None
        self.q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self.ready = threading.Event()
        self.busy: str | None = None
        self.served = 0
        self.expired = 0
        self.load_error: str | None = None

    def submit(self, job: Job, prio: int) -> None:
        self.q.put((prio, next(self._seq), job))

    def run(self) -> None:
        try:
            self.engine = E.Engine()
            for m in self.models:
                s = self.engine.load(m)
                log(f"loaded {m} in {s}s")
        except Exception as e:  # noqa: BLE001
            self.load_error = repr(e)
            log(f"❌ model load failed: {e!r}")
            os._exit(3)          # let launchd restart us; a half-loaded server is worse than none
        self.ready.set()
        while True:
            _prio, _seq, job = self.q.get()
            now = time.monotonic()
            if job.deadline is not None and now > job.deadline:
                job.error = "expired"
                self.expired += 1
                job.done.set()
                continue
            job.started = now
            self.busy = job.model
            try:
                job.result = self.engine.transcribe(job.model, job.audio, job.prompt, job.hotwords)
                self.served += 1
            except Exception as e:  # noqa: BLE001 — a bad clip answers 500, the server stays up
                job.error = f"{e.__class__.__name__}: {e}"[:300]
            finally:
                self.busy = None
                job.audio = None
                job.done.set()


def make_handler(inf: Inference, token: str, started_at: float):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"open-dictate-asr/{VERSION}"

        def log_message(self, *_a):          # no default access log (it would print paths/IPs)
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlparse(self.path).path != "/health":
                return self._send(404, {"ok": False, "error": "not_found"})
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20)
            self._send(200, {
                "ok": inf.ready.is_set(), "version": VERSION, "engine": E.ENGINE_VERSION,
                "models": inf.engine.loaded if inf.engine else {}, "busy": inf.busy,
                "queue": inf.q.qsize(), "served": inf.served, "expired": inf.expired,
                "uptime_s": round(time.monotonic() - started_at), "max_rss_mb": round(rss_mb),
            })

        def do_POST(self):
            t0 = time.monotonic()
            u = urlparse(self.path)
            if u.path != "/v1/transcribe":
                return self._send(404, {"ok": False, "error": "not_found"})
            if not hmac.compare_digest(self.headers.get("X-Token", ""), token):
                return self._send(403, {"ok": False, "error": "forbidden"})
            if not inf.ready.is_set():
                return self._send(503, {"ok": False, "error": "loading"})
            q = parse_qs(u.query)
            model = E.resolve(q.get("model", [inf.models[0]])[0])
            if model not in inf.engine.loaded:
                return self._send(400, {"ok": False, "error": f"model_not_loaded:{model}"})
            prio_name = q.get("priority", ["main"])[0]
            prio = PRIORITY.get(prio_name, 0)
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "bad_length"})
            body = self.rfile.read(n)
            try:
                audio = E.decode_audio(body, self.headers.get("Content-Type", ""))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"ok": False, "error": f"bad_audio:{e}"[:200]})
            prompt = None
            if self.headers.get("X-ASR-Prompt"):
                try:
                    prompt = base64.urlsafe_b64decode(self.headers["X-ASR-Prompt"].encode()).decode("utf-8")
                except Exception:  # noqa: BLE001
                    return self._send(400, {"ok": False, "error": "bad_prompt"})
            hotwords = None
            if self.headers.get("X-ASR-Hotwords"):
                try:
                    hotwords = json.loads(base64.urlsafe_b64decode(self.headers["X-ASR-Hotwords"].encode()))
                    assert isinstance(hotwords, list) and all(isinstance(h, str) for h in hotwords)
                except Exception:  # noqa: BLE001
                    return self._send(400, {"ok": False, "error": "bad_hotwords"})
            deadline = None
            if self.headers.get("X-Deadline-Ms"):
                deadline = t0 + int(self.headers["X-Deadline-Ms"]) / 1000.0
            job = Job(model, audio, prompt, hotwords, deadline)
            dur = len(audio) / E.SAMPLE_RATE
            inf.submit(job, prio)
            job.done.wait(timeout=max(60.0, dur * 2 + 30.0))
            server_ms = int((time.monotonic() - t0) * 1000)
            queue_ms = int(((job.started or time.monotonic()) - job.enq) * 1000)
            if job.result is None:
                err = job.error or "timeout"
                log(f"{prio_name} {model} {dur:.1f}s → {err} (queue {queue_ms}ms)")
                return self._send(503 if err in ("expired", "timeout") else 500, {"ok": False, "error": err})
            r = job.result
            log(f"{prio_name} {model} {dur:.1f}s engine {r['engine_ms']}ms queue {queue_ms}ms total {server_ms}ms")
            self._send(200, {"ok": True, **r, "queue_ms": queue_ms, "server_ms": server_ms})

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=f"open-dictate ASR server v{VERSION}")
    ap.add_argument("--bind", required=True, help="the one address to listen on (tailnet / private)")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--models", default="whisper-v3,qwen3-asr-1.7b")
    ap.add_argument("--token-keychain", help="macOS keychain service name (account 'token')")
    ap.add_argument("--token-env", default="OPEN_DICTATE_ASR_TOKEN", help="env var fallback")
    args = ap.parse_args()
    if args.bind in ("0.0.0.0", "::", ""):
        raise SystemExit("refusing to listen on every interface; pass the tailnet address")
    token = keychain_secret(args.token_keychain) if args.token_keychain else os.environ.get(args.token_env, "")
    if len(token) < 16:
        raise SystemExit("token missing or too short")
    models = [E.resolve(m) for m in args.models.split(",") if m.strip()]
    inf = Inference(models)
    inf.start()
    srv = ThreadingHTTPServer((args.bind, args.port), make_handler(inf, token, time.monotonic()))
    srv.daemon_threads = True
    log(f"asr_server v{VERSION} (engine {E.ENGINE_VERSION}) listening {args.bind}:{args.port}, loading {models}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
