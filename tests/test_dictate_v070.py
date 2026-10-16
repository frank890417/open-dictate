"""daemon v0.7.0: ASR router (remote first, local worker fallback, crash recovery, shadow).

No model is loaded: the remote is the real asr_server handler around a fake engine on
127.0.0.1, and the local worker is a tiny fake script speaking the worker protocol.
All audio is synthetic.
"""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

import asr_engine  # type: ignore[import-not-found]  # noqa: E402
import asr_router  # type: ignore[import-not-found]  # noqa: E402
import asr_server  # type: ignore[import-not-found]  # noqa: E402
import dictated  # type: ignore[import-not-found]  # noqa: E402

TOKEN = "t" * 32

FAKE_WORKER = textwrap.dedent('''
    import json, os, signal, sys
    mode = os.environ.get("FAKE_WORKER_MODE", "ok")
    flag = os.environ.get("FAKE_WORKER_FLAG", "")
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    out.write(json.dumps({"ready": True, "models": {"m": 0.0}}) + "\\n"); out.flush()
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "crash_once" and flag and not os.path.exists(flag):
            open(flag, "w").close()
            os.kill(os.getpid(), signal.SIGSEGV)
        if mode == "hang":
            import time; time.sleep(60)
        out.write(json.dumps({"id": req["id"], "ok": True, "text": "本機聽到的字",
                              "segments": [{"text": "本機聽到的字", "no_speech_prob": 0.0, "temperature": 0.0}],
                              "engine_ms": 5, "model": req["model"]}) + "\\n"); out.flush()
''')


class FakeEngine:
    delay = 0.0
    calls: list = []

    def __init__(self):
        self.loaded = {}

    def load(self, name):
        self.loaded[asr_engine.resolve(name)] = 0.0
        return 0.0

    def transcribe(self, name, audio, prompt=None, hotwords=None):
        FakeEngine.calls.append((name, len(audio), prompt, hotwords))
        time.sleep(FakeEngine.delay)
        text = f"遠端{name}聽到的字"
        return {"text": text, "segments": [{"text": text, "no_speech_prob": 0.0, "temperature": 0.0}],
                "model": name, "engine_ms": int(FakeEngine.delay * 1000)}


def _speech(seconds=2.0):
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t)) * 0.2).astype(np.float32)


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ServerFixture:
    def __init__(self, models=("qwen3-asr-1.7b", "whisper-turbo")):
        self.patch = mock.patch.object(asr_server.E, "Engine", FakeEngine)
        self.patch.start()
        self.inf = asr_server.Inference([asr_engine.resolve(m) for m in models])
        self.inf.start()
        self.inf.ready.wait(5)
        self.port = _free_port()
        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port),
                                       asr_server.make_handler(self.inf, TOKEN, time.monotonic()))
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.patch.stop()


class RouterBase(unittest.TestCase):
    def setUp(self):
        FakeEngine.delay = 0.0
        FakeEngine.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        self.worker = Path(self.tmp.name) / "fake_worker.py"
        self.worker.write_text(FAKE_WORKER, encoding="utf-8")
        self.logs: list[str] = []
        self.worker_patch = mock.patch.object(asr_router, "WORKER_PATH", str(self.worker))
        self.worker_patch.start()
        self.routers = []

    def tearDown(self):
        for r in self.routers:
            r.stop()
        self.worker_patch.stop()
        self.tmp.cleanup()

    def router(self, url="", shadow=""):
        r = asr_router.AsrRouter("qwen3-asr-1.7b", shadow_model=shadow, remote_url=url, token=TOKEN,
                                 worker_python=sys.executable, log=self.logs.append)
        r.start()
        self.assertTrue(r.local.ready.wait(10), "fake worker never became ready")
        self.routers.append(r)
        return r


class RemoteFirst(RouterBase):
    def setUp(self):
        super().setUp()
        self.srv = ServerFixture()

    def tearDown(self):
        self.srv.close()
        super().tearDown()

    def test_remote_answers_and_reports_where(self):
        r = self.router(self.srv.url)
        res, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
        self.assertEqual(info["asr_where"], "studio")
        self.assertIn("遠端qwen3-asr-1.7b", res["text"])
        self.assertIsNone(info["asr_fallback"])
        self.assertGreaterEqual(info["net_ms"], 0)
        self.assertEqual(FakeEngine.calls[0][1], 32000, "the server got the whole clip back from FLAC/WAV")

    def test_timeout_falls_back_to_local(self):
        FakeEngine.delay = 1.5                 # 2 s clip → remote budget 0.7 s
        r = self.router(self.srv.url)
        res, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
        self.assertEqual(info["asr_where"], "local")
        self.assertEqual(res["text"], "本機聽到的字")
        self.assertEqual(info["asr_fallback"], "studio_timeout")

    def test_wrong_token_is_refused(self):
        r = asr_router.RemoteAsr(self.srv.url, "wrong" * 8, self.logs.append)
        body = asr_engine.encode_audio(_speech())
        with self.assertRaises(asr_router.RemoteError) as cm:
            r.transcribe(body[0], body[1], "qwen3-asr-1.7b", None, 2.0)
        self.assertIn("403", str(cm.exception))

    def test_health_needs_no_token_and_shows_models(self):
        h = asr_router.RemoteAsr(self.srv.url, TOKEN, self.logs.append).health()
        self.assertTrue(h["ok"])
        self.assertEqual(set(h["models"]), {"qwen3-asr-1.7b", "whisper-turbo"})
        self.assertEqual(h["engine"], asr_engine.ENGINE_VERSION)

    def test_shadow_goes_to_second_model(self):
        r = self.router(self.srv.url, shadow="whisper-turbo")
        res, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
        got = threading.Event()
        box = {}

        def done(result, sinfo):
            box.update(result=result, info=sinfo)
            got.set()

        r.shadow(_speech(), info["body"], 2.0, None, done, hotwords=["吳哲宇", "TouchDesigner"])
        self.assertTrue(got.wait(5))
        self.assertEqual(FakeEngine.calls[-1][3], ["吳哲宇", "TouchDesigner"], "hotwords cross the wire intact")
        self.assertIn("遠端whisper-turbo", box["result"]["text"])
        self.assertEqual(box["info"]["model"], "whisper-turbo")

    def test_expired_jobs_are_dropped_not_run(self):
        FakeEngine.delay = 0.8
        r = asr_router.RemoteAsr(self.srv.url, TOKEN, self.logs.append)
        body = asr_engine.encode_audio(_speech())
        t = threading.Thread(target=lambda: r.transcribe(body[0], body[1], "qwen3-asr-1.7b", None, 5.0))
        t.start()
        time.sleep(0.1)
        with self.assertRaises(asr_router.RemoteTimeout):
            r.transcribe(body[0], body[1], "qwen3-asr-1.7b", None, 0.3)   # queued behind the first
        t.join()
        time.sleep(0.2)
        self.assertEqual(len(FakeEngine.calls), 1, "the abandoned request never reached the engine")
        self.assertEqual(self.srv.inf.expired, 1)


class RemoteDownAndCrash(RouterBase):
    def test_unreachable_remote_falls_back_then_cools_down(self):
        r = self.router(f"http://127.0.0.1:{_free_port()}")
        _, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
        self.assertEqual((info["asr_where"], info["asr_fallback"]), ("local", "studio_down"))
        _, info2 = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
        self.assertEqual((info2["asr_where"], info2["asr_fallback"]), ("local", "studio_cooling"))

    def test_local_crash_restarts_worker_and_uses_the_other_path(self):
        srv = ServerFixture()
        try:
            flag = Path(self.tmp.name) / "crashed"
            with mock.patch.dict("os.environ", {"FAKE_WORKER_MODE": "crash_once", "FAKE_WORKER_FLAG": str(flag)}):
                r = self.router(srv.url)
            r.remote.down_until = time.monotonic() + 30      # remote "cooling": local goes first
            res, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
            self.assertEqual(info["asr_where"], "studio")
            self.assertIn("local_crash", info["asr_fallback"])
            self.assertTrue(r.local.ready.wait(10), "worker respawned")
            self.assertEqual(r.local.restarts, 1)
            self.assertLess(r.local.last_exit, 0, "died from a signal")
            _, info2 = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
            self.assertEqual(info2["asr_where"], "local")
        finally:
            srv.close()

    def test_loading_worker_is_skipped_when_the_remote_can_take_it(self):
        srv = ServerFixture()
        try:
            FakeEngine.delay = 1.0                     # 2 s clip → remote budget 0.7 s → times out
            r = asr_router.AsrRouter("qwen3-asr-1.7b", remote_url=srv.url, token=TOKEN,
                                     worker_python=sys.executable, log=self.logs.append)
            r.local.ready.clear()                      # worker "still loading" (never started)
            t0 = time.monotonic()
            res, info = r.transcribe(_speech(), 2.0, None, time.monotonic() + 10)
            self.assertEqual(info["asr_where"], "studio", "second try on the remote with the time left")
            self.assertEqual(info["asr_fallback"], "studio_timeout; local_not_ready")
            self.assertLess(time.monotonic() - t0, 4.0)
        finally:
            srv.close()

    def test_hung_worker_is_killed_within_the_deadline(self):
        with mock.patch.dict("os.environ", {"FAKE_WORKER_MODE": "hang"}):
            r = self.router()
        t0 = time.monotonic()
        with self.assertRaises(asr_router.AsrUnavailable) as cm:
            r.transcribe(_speech(), 2.0, None, time.monotonic() + 1.5)
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertIn("local_timeout", str(cm.exception))


class AudioCodec(unittest.TestCase):
    def test_roundtrip_is_lossless_for_pcm16(self):
        a = (np.round(_speech(1.0) * 32767) / 32767).astype(np.float32)
        body, ctype = asr_engine.encode_audio(a)
        b = asr_engine.decode_audio(body, ctype)
        self.assertEqual(len(a), len(b))
        self.assertLess(float(np.abs(a - b).max()), 1e-4)

    def test_model_keys_and_repos_resolve_to_the_same_spec(self):
        self.assertEqual(asr_engine.resolve("mlx-community/Qwen3-ASR-1.7B-8bit"), "qwen3-asr-1.7b")
        self.assertTrue(asr_engine.spec("qwen3-asr-1.7b")["native_punct"])
        self.assertFalse(asr_engine.spec("mlx-community/whisper-large-v3-mlx")["native_punct"])
        self.assertEqual(asr_engine.spec("someone/my-whisper")["kind"], "whisper")


class DaemonWithRouter(unittest.TestCase):
    """handle_transcribe in router mode: log fields, punctuation choice, shadow row."""

    def _daemon(self, native=True, where="studio"):
        class FakeLexicon:
            contextual = []
            replacements = []

            @staticmethod
            def correct(text):
                return text, []

        class FakeRouter:
            main = "qwen3-asr-1.7b" if native else "whisper-turbo"
            shadow_model = "whisper-turbo" if native else "qwen3-asr-1.7b"
            native_punct = native
            shadows = []

            def transcribe(self, audio, dur, prompt, deadline, wav_path=None, body=None, hotwords=None):
                self.prompt = prompt
                self.hotwords = hotwords
                text = "好,我們先看這個作品的聲音,然後再討論燈光" if native else "好 我們先看這個作品的聲音"
                return ({"text": text, "segments": [{"text": text, "no_speech_prob": 0.0, "temperature": 0.0}]},
                        {"asr_where": where, "asr_model": self.main, "asr_engine_ms": 40, "net_ms": 90,
                         "asr_fallback": None if where == "studio" else "studio_timeout", "body": (b"x", "audio/flac")})

            def shadow(self, audio, body, dur, prompt, on_done, hotwords=None):
                self.shadows.append((body, prompt, hotwords))
                on_done({"text": "影子模型的字", "segments": [{"text": "影子模型的字", "no_speech_prob": 0.0}]},
                        {"model": self.shadow_model, "where": "studio", "engine_ms": 30, "net_ms": 80})

        d = object.__new__(dictated.DictationDaemon)
        d.lex = FakeLexicon()
        d.initial_prompt = "LONG"
        d.initial_prompt_short = "SHORT"
        d.hotwords = ["吳哲宇", "Muse"]
        d.router = FakeRouter()
        return d

    def _run(self, d, punct="llm_zh", prompt_policy="none", punct_llm=False):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "speech.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes((_speech(3.0) * 32767).astype(np.int16).tobytes())
            with mock.patch.object(dictated, "LOG_DIR", Path(tmp) / "log"), \
                    mock.patch.object(dictated, "SHADOW_LOG_DIR", Path(tmp) / "shadow"), \
                    mock.patch.object(dictated, "ASR_PROMPT", prompt_policy), \
                    mock.patch.object(dictated, "PUNCT_LLM_ENABLED", punct_llm), \
                    mock.patch.object(dictated, "llm_punct_routed", side_effect=AssertionError("LLM called")), \
                    mock.patch.object(dictated, "log_line"):
                out = d.handle_transcribe({"wav": str(wav), "punct": punct})
            row = json.loads(next((Path(tmp) / "log").glob("*.jsonl")).read_text().splitlines()[-1])
            shadow_files = list((Path(tmp) / "shadow").glob("*.jsonl"))
            shadow = json.loads(shadow_files[0].read_text().splitlines()[-1]) if shadow_files else None
        return out, row, shadow

    def test_native_punct_model_skips_the_llm_and_logs_the_route(self):
        d = self._daemon(native=True)
        out, row, shadow = self._run(d)
        self.assertTrue(out["ok"])
        self.assertEqual(out["punct"], "asr_zh")
        self.assertEqual(out["text"], "好，我們先看這個作品的聲音，然後再討論燈光")
        self.assertEqual(out["asr_where"], "studio")
        self.assertIsNone(d.router.prompt, "ASR_PROMPT=none sends no prompt")
        for k in ("uid", "asr_where", "asr_model", "asr_engine_ms", "net_ms"):
            self.assertIn(k, row)
        self.assertEqual(row["punct_req"], "llm_zh")
        self.assertEqual(shadow["uid"], row["uid"])
        self.assertEqual(shadow["model"], "whisper-turbo")
        self.assertEqual(shadow["text"], "影子模型的字")
        self.assertEqual(shadow["main_text"], out["text"])

    def test_whisper_main_with_llm_disabled_uses_the_rule_layer(self):
        d = self._daemon(native=False, where="local")
        out, row, _ = self._run(d)
        self.assertEqual(out["punct"], "smart_zh")
        self.assertEqual(row["asr_where"], "local")
        self.assertEqual(row["asr_fallback"], "studio_timeout")

    def test_raw_mode_stays_raw(self):
        out, _, _ = self._run(self._daemon(native=True), punct="raw")
        self.assertEqual(out["punct"], "raw")
        self.assertIn(",", out["text"])

    def test_full_prompt_policy_still_sends_the_prompt(self):
        d = self._daemon(native=False)
        self._run(d, prompt_policy="full")
        self.assertEqual(d.router.prompt, "SHORT", "3 s clip → short prompt, as before")
        self.assertIsNone(d.router.hotwords)
        self.assertEqual(d.router.shadows[-1][1:], (None, ["吳哲宇", "Muse"]), "Qwen shadow gets hotwords")

    def test_qwen_main_gets_hotwords_not_a_prompt(self):
        d = self._daemon(native=True)
        self._run(d, prompt_policy="full")
        self.assertIsNone(d.router.prompt)
        self.assertEqual(d.router.hotwords, ["吳哲宇", "Muse"])
        self.assertEqual(d.router.shadows[-1][1:], ("SHORT", None), "whisper shadow gets the prompt")


class Version(unittest.TestCase):
    def test_version(self):
        self.assertEqual(dictated.__version__, "0.7.0")


if __name__ == "__main__":
    unittest.main()
