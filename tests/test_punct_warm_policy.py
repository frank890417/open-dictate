"""daemon 0.7.2: the LLM punctuation model is warmed only when it is needed.

Until 0.7.1 the daemon loaded the Ollama punctuation model (21 GB for the default
`qwen3.6:35b-a3b-coding-nvfp4`) as soon as it started, whatever the mode: fast mode never uses LLM
punctuation, and an accurate-mode user who never picked `llm_zh` does not either. Now:

    PUNCT_WARM unset / auto   warm only once a sentence really goes to the LLM (accurate mode + llm_zh);
                              switching to fast lets go (no reload, no unload)
    PUNCT_WARM=1              load at start and keep resident, as 0.6.0 to 0.7.1 did
    PUNCT_WARM=0              never

Nothing here talks to a real Ollama: a stub HTTP server on 127.0.0.1 records every request, and the
daemon is pointed at it (in a child process for the start-up cases, so the old code can be run
against the same assertions).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
os.environ.setdefault("OPEN_DICTATE_PUNCT_LLM_URL", "http://127.0.0.1:9/api/chat")
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

import dictated  # type: ignore[import-not-found]  # noqa: E402


class StubOllama:
    """Records (method, path); /api/ps says no model is loaded, /api/chat answers at once."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 — silence
                pass

            def _reply(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                self._reply({"models": []})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append(("POST", self.path))
                self._reply({"message": {"content": "ok"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/chat"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self, method: str | None = None) -> int:
        return sum(1 for m, _ in self.requests if method in (None, m))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def wait_until(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class PolicyValues(unittest.TestCase):
    def test_parsing(self):
        for v in (None, "", "auto", "AUTO", "whatever"):
            self.assertEqual(dictated.punct_warm_policy(v), "auto", v)
        for v in ("1", "true", "Yes", "on", "eager"):
            self.assertEqual(dictated.punct_warm_policy(v), "eager", v)
        for v in ("0", "false", "No", "off"):
            self.assertEqual(dictated.punct_warm_policy(v), "off", v)


class StartUp(unittest.TestCase):
    """The daemon process, as launchd starts it: main() calls PUNCT_WARMER.start() and nothing else here.

    Run in a child process so the same lines also run against the 0.7.1 code (they use only
    PUNCT_WARMER.start(), which both have): 0.7.1 fails the default-policy test, 0.7.2 passes."""

    CHILD = textwrap.dedent('''
        import sys, time
        sys.path.insert(0, {daemon!r})
        import dictated
        dictated.PUNCT_WARMER.start()
        time.sleep(1.5)
    ''')

    def start_daemon_side(self, warm_env):
        stub = StubOllama()
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPEN_DICTATE_PUNCT")}
        env["OPEN_DICTATE_PUNCT_LLM_URL"] = stub.url
        if warm_env is not None:
            env["OPEN_DICTATE_PUNCT_WARM"] = warm_env
        with tempfile.TemporaryDirectory(prefix="od-warm-test-") as data_root:
            env["OPEN_DICTATE_DATA_ROOT"] = data_root
            subprocess.run([sys.executable, "-c", self.CHILD.format(daemon=str(DAEMON_DIR))], env=env, timeout=60, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        n = stub.count()
        posts = stub.count("POST")
        stub.close()
        return n, posts

    def test_default_start_does_not_touch_ollama(self):
        n, posts = self.start_daemon_side(None)
        self.assertEqual((n, posts), (0, 0), "0.7.1 asked /api/ps at start and loaded the model (POST /api/chat)")

    def test_explicit_one_still_loads_at_start(self):
        n, posts = self.start_daemon_side("1")
        self.assertGreaterEqual(posts, 1, "PUNCT_WARM=1 keeps the 0.6.0 to 0.7.1 behaviour: load at start")

    def test_zero_never_touches_ollama(self):
        self.assertEqual(self.start_daemon_side("0"), (0, 0))


class Warmer(unittest.TestCase):
    def setUp(self):
        self.stub = StubOllama()
        p = mock.patch.object(dictated, "PUNCT_LLM_URL", self.stub.url)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.stub.close)
        lp = mock.patch.object(dictated, "log_line")
        lp.start()
        self.addCleanup(lp.stop)

    def test_auto_waits_for_the_first_llm_sentence(self):
        w = dictated.PunctWarmer("auto")
        w.start()
        time.sleep(0.5)
        self.assertEqual(self.stub.count(), 0)
        self.assertFalse(w.wanted)
        self.assertFalse(w._started)
        w.want()
        self.assertTrue(wait_until(lambda: self.stub.count("POST") >= 1), "want() must start loading")
        st = w.status()
        self.assertEqual((st["policy"], st["wanted"], st["enabled"]), ("auto", True, True))

    def test_release_stops_reloading_but_never_unloads(self):
        w = dictated.PunctWarmer("auto")
        w.want()
        self.assertTrue(wait_until(lambda: self.stub.count("POST") >= 1))
        before = list(self.stub.requests)
        w.release()
        self.assertFalse(w.wanted)
        w._tick()                                  # the keeper's periodic check, model "not loaded" per the stub
        self.assertEqual(self.stub.requests, before, "after release the keeper must not even ask Ollama")
        w.want()                                   # back to accurate + llm_zh
        w._tick()
        self.assertGreater(len(self.stub.requests), len(before))

    def test_eager_ignores_release(self):
        w = dictated.PunctWarmer("eager")
        self.assertTrue(w.wanted)
        w.release()
        self.assertTrue(w.wanted, "an explicit PUNCT_WARM=1 is the user's choice and stays")
        w._tick()
        self.assertGreaterEqual(self.stub.count("POST"), 1)

    def test_off_never_asks_and_never_blocks_the_request_path(self):
        w = dictated.PunctWarmer("off")
        w.start()
        w.want()
        w._tick()
        self.assertEqual(self.stub.count(), 0)
        self.assertTrue(w.is_ready(), "off keeps the old direct-call behaviour")


class DaemonPath(unittest.TestCase):
    """handle_transcribe / handle_set_mode decide when the warmer is wanted."""

    def daemon(self, native: bool):
        class FakeLexicon:
            contextual = []
            replacements = []

            @staticmethod
            def correct(text):
                return text, []

        class FakeRouter:
            main = "qwen3-asr-1.7b" if native else "whisper-turbo"
            shadow_model = ""

            def __init__(self):
                self.models = None

            def transcribe(self, audio, dur, prompt, deadline, wav_path=None, body=None, hotwords=None, model=None):
                text = "好,我們先看這個作品的聲音"
                return ({"text": text, "segments": [{"text": text, "no_speech_prob": 0.0, "temperature": 0.0}]},
                        {"asr_where": "local", "asr_model": self.main, "asr_engine_ms": 40, "net_ms": 0, "body": (b"x", "audio/flac")})

            def set_models(self, main, shadow):
                self.models = (main, shadow)
                self.main = main

        d = object.__new__(dictated.DictationDaemon)
        d.lex = FakeLexicon()
        d.initial_prompt = d.initial_prompt_short = "P"
        d.hotwords = []
        d.router = FakeRouter()
        d.modes = {"fast": "qwen3-asr-1.7b", "accurate": "whisper-turbo"}
        d.mode = "fast" if native else "accurate"
        return d

    def transcribe(self, d, warmer, punct="llm_zh"):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "s.wav"
            t = np.arange(int(3.0 * 16000)) / 16000
            audio = (0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes((audio * 32767).astype(np.int16).tobytes())
            with mock.patch.object(dictated, "LOG_DIR", Path(tmp) / "log"), \
                    mock.patch.object(dictated, "PUNCT_LLM_ENABLED", True), \
                    mock.patch.object(dictated, "PUNCT_WARMER", warmer), \
                    mock.patch.object(dictated, "llm_punct_routed", return_value=("好，我們先看這個作品的聲音", "llm_zh")) as llm, \
                    mock.patch.object(dictated, "log_line"):
                out = d.handle_transcribe({"wav": str(wav), "punct": punct})
        return out, llm

    def warmer(self):
        w = dictated.PunctWarmer("auto")
        w._start_thread = lambda: None            # no thread, no HTTP: this class only checks who asks for the model
        return w

    def test_fast_mode_never_wants_the_llm_even_when_the_shell_asks_for_llm_punctuation(self):
        d, w = self.daemon(native=True), self.warmer()
        out, llm = self.transcribe(d, w, "llm_zh")
        self.assertTrue(out["ok"])
        self.assertEqual(out["punct"], "asr_zh")
        llm.assert_not_called()
        self.assertFalse(w.wanted, "fast mode must not wake the punctuation model")

    def test_accurate_mode_with_llm_zh_wants_it(self):
        d, w = self.daemon(native=False), self.warmer()
        out, llm = self.transcribe(d, w, "llm_zh")
        llm.assert_called_once()
        self.assertTrue(w.wanted)

    def test_accurate_mode_with_smart_zh_does_not(self):
        d, w = self.daemon(native=False), self.warmer()
        self.transcribe(d, w, "smart_zh")
        self.assertFalse(w.wanted, "an accurate-mode user who never picked LLM punctuation never loads the model")

    def test_switching_modes_follows_what_the_shell_asked_for(self):
        d, w = self.daemon(native=True), self.warmer()
        self.transcribe(d, w, "llm_zh")                      # fast mode, shell set to LLM punctuation
        self.assertFalse(w.wanted)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dictated, "MODE_STATE_PATH", Path(tmp) / "m.json"), \
                mock.patch.object(dictated, "PUNCT_WARMER", w), mock.patch.object(dictated, "log_line"):
            self.assertTrue(d.handle_set_mode({"mode": "accurate"})["ok"])
            self.assertTrue(w.wanted, "switching to accurate after llm_zh was seen warms the model")
            self.assertTrue(d.handle_set_mode({"mode": "fast"})["ok"])
            self.assertFalse(w.wanted, "switching to fast lets go")

    def test_switching_to_accurate_without_llm_zh_does_not_warm(self):
        d, w = self.daemon(native=True), self.warmer()
        self.transcribe(d, w, "smart_zh")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dictated, "MODE_STATE_PATH", Path(tmp) / "m.json"), \
                mock.patch.object(dictated, "PUNCT_WARMER", w), mock.patch.object(dictated, "log_line"):
            d.handle_set_mode({"mode": "accurate"})
        self.assertFalse(w.wanted)

    def test_ping_reports_the_policy(self):
        st = dictated.PunctWarmer("auto").status()
        for key in ("enabled", "policy", "wanted", "loading", "ready_age_s", "last_load_s", "last_error"):
            self.assertIn(key, st)


if __name__ == "__main__":
    unittest.main()
