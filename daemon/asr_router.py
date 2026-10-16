"""ASR routing for dictated (ASR_BACKEND=router): remote first, local worker as fallback.

Order for each utterance:
  1. Remote recognition service (asr_server.py on another Mac on the private network), if
     configured and not cooling down. Timeout = clip length × RTF + base (default
     ×0.1 + 0.5 s): a remote that cannot beat that is slower than just running locally.
  2. Local worker subprocess (asr_worker.py) running the same model through the same
     asr_engine. If it crashes (MLX segfault) or hangs, it is restarted in the background
     and the utterance goes back to the remote with whatever time is left.
Shadow: optionally the same clip is sent to a second model on the remote after the main
result is back; the result goes to a callback (the daemon logs it), never to the user.

Nothing here logs transcripts; the daemon decides what goes into its private log.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

try:
    from . import asr_engine as E
except ImportError:  # direct script execution
    import asr_engine as E

WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asr_worker.py")


class AsrUnavailable(RuntimeError):
    """Neither path produced a result; str() lists what was tried."""


class RemoteError(RuntimeError):
    kind = "error"


class RemoteDown(RemoteError):
    kind = "down"


class RemoteTimeout(RemoteError):
    kind = "timeout"


def keychain_secret(service: str, account: str = "token") -> str | None:
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


# ---------------------------------------------------------------------------
# Remote
# ---------------------------------------------------------------------------
class RemoteAsr:
    CONNECT_TIMEOUT_S = 0.6       # LAN / direct tailnet connects in 5–150 ms
    COOLDOWN_S = 30.0             # after "down": skip the remote, don't pay a connect per utterance
    TIMEOUT_STREAK = 3            # this many timeouts in a row also cools down (busy / relayed link)

    def __init__(self, url: str, token: str, log) -> None:
        u = urlsplit(url)
        self.host, self.port = u.hostname, u.port or 80
        self.token = token
        self.log = log
        self.down_until = 0.0
        self.timeout_streak = 0
        self.last_error: str | None = None
        self.last_ok_at: float | None = None
        self.healthy_at: float | None = None
        self.engine_version: str | None = None

    def available(self) -> bool:
        return time.monotonic() >= self.down_until

    def _cool(self, why: str) -> None:
        self.down_until = time.monotonic() + self.COOLDOWN_S
        self.log(f"asr remote {self.host}:{self.port} cooling {self.COOLDOWN_S:.0f}s ({why})")

    def health(self, timeout: float = 1.5) -> dict | None:
        try:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
            conn.request("GET", "/health")
            r = conn.getresponse()
            data = json.loads(r.read() or b"{}")
            conn.close()
            self.engine_version = data.get("engine")
            if data.get("ok"):
                self.healthy_at = time.monotonic()
            return data
        except (OSError, ValueError, http.client.HTTPException):
            return None

    def transcribe(self, body: bytes, ctype: str, model: str, prompt: str | None,
                   timeout: float, priority: str = "main", hotwords: list[str] | None = None) -> dict:
        """POST one clip. Raises RemoteDown / RemoteTimeout / RemoteError. Adds rt_ms."""
        t0 = time.monotonic()
        deadline = t0 + timeout
        headers = {"X-Token": self.token, "Content-Type": ctype,
                   "X-Deadline-Ms": str(int(timeout * 1000))}
        if prompt:
            headers["X-ASR-Prompt"] = base64.urlsafe_b64encode(prompt.encode("utf-8")).decode()
        if hotwords:
            headers["X-ASR-Hotwords"] = base64.urlsafe_b64encode(
                json.dumps(list(hotwords), ensure_ascii=False).encode("utf-8")).decode()
        conn = http.client.HTTPConnection(self.host, self.port,
                                          timeout=max(0.05, min(timeout, self.CONNECT_TIMEOUT_S)))
        try:
            try:
                conn.connect()
            except socket.timeout as e:
                raise RemoteDown("connect_timeout") from e
            except OSError as e:          # refused / no route / host down
                raise RemoteDown(e.__class__.__name__) from e
            conn.sock.settimeout(max(0.05, deadline - time.monotonic()))
            conn.request("POST", f"/v1/transcribe?model={model}&priority={priority}", body=body, headers=headers)
            conn.sock.settimeout(max(0.05, deadline - time.monotonic()))
            resp = conn.getresponse()
            data = resp.read()
        except socket.timeout as e:
            raise RemoteTimeout("timeout") from e
        except (ConnectionError, http.client.HTTPException) as e:   # server died mid-request
            raise RemoteError(f"reset:{e.__class__.__name__}") from e
        finally:
            conn.close()
        try:
            r = json.loads(data or b"{}")
        except ValueError as e:
            raise RemoteError(f"http_{resp.status}_bad_json") from e
        if resp.status != 200 or not r.get("ok"):
            raise RemoteError(f"http_{resp.status}:{r.get('error')}")
        r["rt_ms"] = int((time.monotonic() - t0) * 1000)
        return r

    def note(self, exc: BaseException | None) -> None:
        """Bookkeeping after a main-path call (shadow calls don't move the breaker)."""
        if exc is None:
            self.timeout_streak = 0
            self.last_error = None
            self.last_ok_at = time.monotonic()
            return
        self.last_error = str(exc)
        if isinstance(exc, RemoteDown):
            self._cool(str(exc))
        elif isinstance(exc, RemoteTimeout):
            self.timeout_streak += 1
            if self.timeout_streak >= self.TIMEOUT_STREAK:
                self.timeout_streak = 0
                self._cool(f"{self.TIMEOUT_STREAK} timeouts in a row")

    def status(self) -> dict:
        now = time.monotonic()
        return {"url": f"{self.host}:{self.port}", "available": self.available(),
                "cooling_s": round(max(0.0, self.down_until - now), 1),
                "last_ok_age_s": round(now - self.last_ok_at, 1) if self.last_ok_at else None,
                "last_error": self.last_error, "engine": self.engine_version}


# ---------------------------------------------------------------------------
# Local worker
# ---------------------------------------------------------------------------
class WorkerError(RuntimeError):
    kind = "error"


class WorkerCrashed(WorkerError):
    kind = "crash"


class WorkerTimeout(WorkerError):
    kind = "timeout"


class WorkerNotReady(WorkerError):
    kind = "not_ready"


class LocalWorker:
    CRASH_WINDOW_S = 120.0
    CRASH_LIMIT = 3               # more crashes than this in the window → wait before respawning
    BACKOFF_S = 30.0

    def __init__(self, python: str, models: list[str], log) -> None:
        self.python = python
        self.models = models
        self.log = log
        self.proc: subprocess.Popen | None = None
        self.ready = threading.Event()
        self._cond = threading.Condition()
        self._responses: dict[int, dict] = {}
        self._next_id = 0
        self._call_lock = threading.Lock()
        self._stopping = False
        self._crashes: list[float] = []
        self.restarts = 0
        self.last_exit: int | None = None
        self.load_s: dict | None = None

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._spawn()

    def stop(self) -> None:
        self._stopping = True
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.close()
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                p.kill()

    def _spawn(self) -> None:
        self.ready.clear()
        cmd = [self.python, "-u", WORKER_PATH, "--models", ",".join(self.models)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                                     text=True, bufsize=1, encoding="utf-8")
        self.log(f"asr local worker spawned (pid {self.proc.pid}, {','.join(self.models)})")
        threading.Thread(target=self._reader, args=(self.proc,), name="asr-worker-reader", daemon=True).start()

    def _reader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ready"):
                self.load_s = msg.get("models")
                self.log(f"asr local worker ready (pid {proc.pid}, load {self.load_s})")
                self.ready.set()
                continue
            with self._cond:
                self._responses[msg.get("id")] = msg
                self._cond.notify_all()
        code = proc.wait()
        for pipe in (proc.stdout, proc.stdin):
            try:
                pipe.close()
            except Exception:  # noqa: BLE001
                pass
        with self._cond:
            if proc is self.proc:
                self.ready.clear()
            self._cond.notify_all()
        if self._stopping or proc is not self.proc:
            return
        self.last_exit = code
        now = time.monotonic()
        self._crashes = [t for t in self._crashes if now - t < self.CRASH_WINDOW_S] + [now]
        wait = self.BACKOFF_S if len(self._crashes) > self.CRASH_LIMIT else 0.0
        self.log(f"⚠️ asr local worker exited (code {code}) → respawn in {wait:.0f}s")
        if wait:
            time.sleep(wait)
        if not self._stopping:
            self.restarts += 1
            self._spawn()

    def _kill(self) -> None:
        p = self.proc
        if p and p.poll() is None:
            p.kill()             # reader thread sees EOF and respawns

    # -------------------------------------------------------------- calls
    def call(self, req: dict, timeout: float, wait_ready: float | None = None) -> dict:
        """wait_ready: how long to wait for a (re)loading worker; None = the whole timeout."""
        deadline = time.monotonic() + timeout
        w = timeout if wait_ready is None else min(wait_ready, timeout)
        if not self.ready.wait(timeout=max(0.0, w)):
            raise WorkerNotReady("not_ready")
        with self._call_lock:
            proc = self.proc
            self._next_id += 1
            rid = self._next_id
            try:
                proc.stdin.write(json.dumps({"id": rid, **req}, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise WorkerCrashed("pipe") from e
            with self._cond:
                while rid not in self._responses:
                    if proc.poll() is not None:
                        raise WorkerCrashed(f"exit {proc.returncode}")
                    left = deadline - time.monotonic()
                    if left <= 0:
                        self.log(f"⚠️ asr local worker stuck past {timeout:.1f}s → killing it")
                        self._kill()
                        raise WorkerTimeout("timeout")
                    self._cond.wait(timeout=min(left, 0.5))
                msg = self._responses.pop(rid)
        if not msg.get("ok"):
            raise WorkerError(msg.get("error") or "error")
        return msg

    def status(self) -> dict:
        p = self.proc
        return {"ready": self.ready.is_set(), "pid": p.pid if p and p.poll() is None else None,
                "restarts": self.restarts, "last_exit": self.last_exit, "load_s": self.load_s}


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class AsrRouter:
    def __init__(self, main_model: str, *, shadow_model: str = "", remote_url: str = "",
                 token: str | None = None, worker_python: str | None = None, log=print,
                 remote_rtf: float = 0.1, remote_base_s: float = 0.5) -> None:
        self.main = E.resolve(main_model)
        self.shadow_model = E.resolve(shadow_model) if shadow_model else ""
        self.log = log
        self.remote_rtf, self.remote_base_s = remote_rtf, remote_base_s
        self.remote = RemoteAsr(remote_url, token, log) if remote_url and token else None
        if remote_url and not token:
            log("⚠️ asr remote configured but no token → local only")
        self.local = LocalWorker(worker_python or sys.executable, [self.main], log)
        self._shadow_q: queue.Queue = queue.Queue(maxsize=4)
        if self.remote and self.shadow_model:
            threading.Thread(target=self._shadow_loop, name="asr-shadow", daemon=True).start()

    @property
    def native_punct(self) -> bool:
        return bool(E.spec(self.main)["native_punct"])

    def start(self) -> None:
        self.local.start()
        if self.remote:
            def probe():
                h = self.remote.health()
                if h is None:
                    self.log(f"asr remote {self.remote.host}:{self.remote.port} not answering at start")
                else:
                    if h.get("engine") != E.ENGINE_VERSION:
                        self.log(f"⚠️ asr remote engine {h.get('engine')} ≠ local {E.ENGINE_VERSION}")
                    self.log(f"asr remote ok: models {list((h.get('models') or {}).keys())}")
            threading.Thread(target=probe, name="asr-remote-probe", daemon=True).start()

    def stop(self) -> None:
        self.local.stop()

    def wait_ready(self, timeout: float) -> bool:
        """Startup: ready when the local worker is warm or the remote answers health."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.local.ready.wait(timeout=1.0):
                return True
            if self.remote and (self.remote.health(timeout=0.5) or {}).get("ok"):
                return True
        return False

    @property
    def warm(self) -> bool:
        """Can transcribe right now without loading a model: local worker warm, or the remote answered."""
        return self.local.ready.is_set() or bool(self.remote and self.remote.available()
                                                  and (self.remote.last_ok_at or self.remote.healthy_at))

    def status(self) -> dict:
        return {"main": self.main, "shadow": self.shadow_model or None,
                "remote": self.remote.status() if self.remote else None, "local": self.local.status()}

    def remote_timeout(self, dur: float) -> float:
        return dur * self.remote_rtf + self.remote_base_s

    # -------------------------------------------------------------- main path
    def transcribe(self, audio, dur: float, prompt: str | None, deadline: float,
                   wav_path: str | None = None, body: tuple[bytes, str] | None = None,
                   hotwords: list[str] | None = None) -> tuple[dict, dict]:
        """audio: float32 array (or None → only the local worker, decoding wav_path).

        Returns (result, info). result has text/segments; info has asr_where / asr_model /
        asr_engine_ms / net_ms / asr_fallback (what failed before the path that answered)
        and "body" (the encoded clip, reused for the shadow request).
        Raises AsrUnavailable when nothing answered before ``deadline`` (monotonic).
        """
        reasons: list[str] = []
        remote_ok = self.remote is not None and audio is not None

        def remote_try(timeout: float) -> tuple[dict, dict] | None:
            nonlocal body
            t0 = time.monotonic()
            if body is None:
                body = E.encode_audio(audio)
            try:
                r = self.remote.transcribe(body[0], body[1], self.main, prompt, timeout, hotwords=hotwords)
            except RemoteError as e:
                self.remote.note(e)
                reasons.append(f"studio_{e.kind}" if e.kind != "error" else f"studio:{e}")
                return None
            self.remote.note(None)
            wall = int((time.monotonic() - t0) * 1000)
            return r, {"asr_where": "studio", "asr_model": self.main, "asr_engine_ms": r.get("engine_ms"),
                       "net_ms": max(0, wall - int(r.get("server_ms") or 0)),
                       "asr_queue_ms": r.get("queue_ms") or None}

        if remote_ok:
            if self.remote.available():
                t = min(self.remote_timeout(dur), deadline - time.monotonic() - 0.3)
                if t > 0.2:
                    got = remote_try(t)
                    if got:
                        return self._done(got, reasons, body)
            else:
                reasons.append("studio_cooling")

        req: dict = {"model": self.main, "prompt": prompt, "hotwords": hotwords}
        tmp = None
        try:
            if audio is not None:
                fd, tmp = tempfile.mkstemp(prefix="dictate-asr-", suffix=".f32")
                with os.fdopen(fd, "wb") as f:
                    audio.astype("float32").tofile(f)
                req["f32"] = tmp
            else:
                req["wav"] = wav_path
            try:
                t0 = time.monotonic()
                # 本機還在載入／重啟中：有遠端可退就不等它（等到截止就沒時間回頭試遠端了）
                msg = self.local.call(req, timeout=max(0.5, deadline - time.monotonic()),
                                      wait_ready=0.0 if remote_ok else None)
                wall = int((time.monotonic() - t0) * 1000)
                return self._done((msg, {"asr_where": "local", "asr_model": self.main,
                                         "asr_engine_ms": msg.get("engine_ms"), "net_ms": 0,
                                         "ipc_ms": max(0, wall - int(msg.get("engine_ms") or 0))}), reasons, body)
            except WorkerError as e:
                reasons.append(f"local_{e.kind}" if e.kind != "error" else f"local:{e}")
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        # The local path broke: take whatever time is left back to the remote, cooling or not.
        if remote_ok:
            left = deadline - time.monotonic() - 0.3
            if left > 0.3:
                got = remote_try(left)
                if got:
                    return self._done(got, reasons, body)
        raise AsrUnavailable("; ".join(reasons) or "no_path")

    @staticmethod
    def _done(got: tuple[dict, dict], reasons: list[str], body) -> tuple[dict, dict]:
        r, info = got
        info["asr_fallback"] = "; ".join(reasons) or None
        info["body"] = body
        return {"text": r.get("text") or "", "segments": r.get("segments") or []}, info

    # -------------------------------------------------------------- shadow
    def shadow(self, audio, body: tuple[bytes, str] | None, dur: float, prompt: str | None, on_done,
               hotwords: list[str] | None = None) -> None:
        """Queue the same clip for the shadow model on the remote; on_done(result | None, info)."""
        if not (self.remote and self.shadow_model) or audio is None:
            return
        if not self.remote.available():
            on_done(None, {"error": "studio_cooling", "model": self.shadow_model})
            return
        try:
            self._shadow_q.put_nowait((audio, body, dur, prompt, hotwords, on_done))
        except queue.Full:
            on_done(None, {"error": "shadow_queue_full", "model": self.shadow_model})

    def _shadow_loop(self) -> None:
        while True:
            audio, body, dur, prompt, hotwords, on_done = self._shadow_q.get()
            info: dict = {"model": self.shadow_model, "where": "studio"}
            result = None
            try:
                if body is None:
                    body = E.encode_audio(audio)
                t0 = time.monotonic()
                r = self.remote.transcribe(body[0], body[1], self.shadow_model, prompt,
                                           timeout=max(15.0, dur * 0.5 + 10.0), priority="shadow",
                                           hotwords=hotwords)
                wall = int((time.monotonic() - t0) * 1000)
                info.update(engine_ms=r.get("engine_ms"), net_ms=max(0, wall - int(r.get("server_ms") or 0)),
                            queue_ms=r.get("queue_ms"))
                result = {"text": r.get("text") or "", "segments": r.get("segments") or []}
            except Exception as e:  # noqa: BLE001 — shadow never affects dictation
                info["error"] = str(e)[:200]
            try:
                on_done(result, info)
            except Exception as e:  # noqa: BLE001
                self.log(f"⚠️ shadow callback failed: {e!r}")
