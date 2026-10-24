"""daemon v0.9.0: redo the last sentence in the other mode, and a result file the shell can fall back on.

- ``RecentAudio`` keeps each successful recording for ``RECENT_AUDIO_S`` seconds (router mode only),
  private to the user, and deletes it when it expires.
- ``retranscribe`` runs one of those recordings through the other mode's model.
- ``ResultStore`` keeps the last few results so a shell that stopped waiting can still find them.

No model is loaded and all audio is synthetic.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

# The suite must never reach a real Ollama (the warm keeper would load a 21 GB model on a developer's Mac): port 9 refuses at once.
os.environ.setdefault("OPEN_DICTATE_PUNCT_LLM_URL", "http://127.0.0.1:9/api/chat")
import dictated  # type: ignore[import-not-found]  # noqa: E402


def _speech(seconds=3.0):
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t)) * 0.2).astype(np.float32)


def _write_wav(path: Path, audio) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class RecentAudioStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "recent"
        patcher = mock.patch.object(dictated, "log_line")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_audio_reads_back_unchanged_and_private(self):
        audio = (np.random.default_rng(1).integers(-32768, 32767, 16000).astype(np.float32)) / 32768.0
        store = dictated.RecentAudio(self.root, 60)
        self.assertTrue(store.save("abc123def456", audio))
        path = store.get("abc123def456")
        self.assertIsNotNone(path)
        back, dur = dictated.load_wav(path)
        self.assertEqual(dur, 1.0)
        np.testing.assert_array_equal(back, audio)
        self.assertEqual(_mode(Path(path)), 0o600)
        self.assertEqual(_mode(self.root), 0o700)

    def test_expired_audio_is_gone(self):
        store = dictated.RecentAudio(self.root, 60)
        store.save("aaaaaa000001", _speech(1.0))
        p = store.path("aaaaaa000001")
        old = time.time() - 61
        os.utime(p, (old, old))
        self.assertIsNone(store.get("aaaaaa000001"))
        self.assertFalse(p.exists(), "reading an expired clip deletes it")

    def test_timer_deletes_without_any_further_request(self):
        store = dictated.RecentAudio(self.root, 0.3)
        store.save("bbbbbb000002", _speech(0.5))
        self.assertTrue(store.path("bbbbbb000002").exists())
        time.sleep(1.3)
        self.assertFalse(store.path("bbbbbb000002").exists())

    def test_start_clears_what_a_previous_run_left(self):
        self.root.mkdir()
        (self.root / "cccccc000003.wav").write_bytes(b"x")
        (self.root / "notes.txt").write_text("not ours")
        dictated.RecentAudio(self.root, 60)
        self.assertFalse((self.root / "cccccc000003.wav").exists())
        self.assertTrue((self.root / "notes.txt").exists(), "only clips are swept")

    def test_bad_uid_and_disabled_store(self):
        store = dictated.RecentAudio(self.root, 60)
        self.assertIsNone(store.get("../../etc/passwd"))
        self.assertFalse(store.save("../x", _speech(0.5)))
        off = dictated.RecentAudio(self.root, 0)
        self.assertFalse(off.save("dddddd000004", _speech(0.5)))
        self.assertFalse(off.path("dddddd000004").exists())


class ResultFile(unittest.TestCase):
    def test_newest_first_capped_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last.json"
            store = dictated.ResultStore(path, keep=3)
            for i in range(5):
                store.record({"uid": f"u{i}", "wav": f"/tmp/rec-{i}.wav", "ok": True, "text": f"第{i}句"})
            rows = store.read()
            self.assertEqual([r["uid"] for r in rows], ["u4", "u3", "u2"])
            self.assertEqual(_mode(path), 0o600)
            self.assertEqual(json.loads(path.read_text())["version"], 1)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["last.json"], "no temp file left behind")

    def test_unreadable_file_starts_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last.json"
            path.write_text("{not json")
            store = dictated.ResultStore(path)
            self.assertEqual(store.read(), [])
            with mock.patch.object(dictated, "log_line"):
                store.record({"uid": "u1", "ok": False, "error": "no_speech"})
            self.assertEqual(store.read()[0]["error"], "no_speech")


class Retranscribe(unittest.TestCase):
    """transcribe saves the clip and returns a uid; retranscribe runs it again in the other mode."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(dictated, "LOG_DIR", base / "log"),
            mock.patch.object(dictated, "SHADOW_LOG_DIR", base / "shadow"),
            mock.patch.object(dictated, "ASR_PROMPT", "full"),
            mock.patch.object(dictated, "PUNCT_LLM_ENABLED", False),
            mock.patch.object(dictated, "log_line"),
        ]
        for p in self.patches:
            p.start()
        self.base = base
        self.d = self._daemon(base)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    @staticmethod
    def _daemon(base: Path):
        class FakeLexicon:
            contextual = []
            replacements = []

            @staticmethod
            def correct(text):
                return text, []

        class FakeRouter:
            main = "qwen3-asr-1.7b"
            shadow_model = ""
            calls = []

            def transcribe(self, audio, dur, prompt, deadline, wav_path=None, body=None, hotwords=None, model=None):
                self.calls.append({"model": model, "prompt": prompt, "hotwords": hotwords, "n": len(audio)})
                text = "準模式聽到的字" if model == "whisper-v3" else "快模式聽到的字"
                return ({"text": text, "segments": [{"text": text, "no_speech_prob": 0.0, "temperature": 0.0}]},
                        {"asr_where": "studio", "asr_model": model, "asr_engine_ms": 40, "net_ms": 9, "body": None})

            def shadow(self, *a, **k):
                raise AssertionError("shadow is off in this test")

        d = object.__new__(dictated.DictationDaemon)
        d.lex = FakeLexicon()
        d.initial_prompt = "LONG"
        d.initial_prompt_short = "SHORT"
        d.hotwords = ["吳哲宇", "Muse"]
        d.router = FakeRouter()
        d.modes = {"fast": "qwen3-asr-1.7b", "accurate": "whisper-v3"}
        d.mode = "fast"
        d.warm = True
        d.recent = dictated.RecentAudio(base / "recent", 60)
        d.results = dictated.ResultStore(base / "last.json")
        return d

    def _say(self, seconds=3.0, **extra):
        wav = Path(tempfile.mkdtemp(dir=self.base)) / "rec.wav"
        _write_wav(wav, _speech(seconds))
        return wav, self.d.handle({"cmd": "transcribe", "wav": str(wav), "punct": "smart_zh", **extra})

    def _log_rows(self):
        return [json.loads(x) for x in next((self.base / "log").glob("*.jsonl")).read_text().splitlines()]

    def test_transcribe_returns_uid_keeps_the_clip_and_records_the_result(self):
        wav, out = self._say()
        self.assertTrue(out["ok"])
        self.assertRegex(out["uid"], r"^[0-9a-f]{12}$")
        self.assertIsNotNone(self.d.recent.get(out["uid"]))
        row = self.d.results.read()[0]
        self.assertEqual((row["uid"], row["wav"], row["text"], row["ok"]), (out["uid"], str(wav), out["text"], True))

    def test_retranscribe_uses_the_accurate_model_on_the_same_clip(self):
        _, first = self._say(3.0)
        out = self.d.handle({"cmd": "retranscribe", "uid": first["uid"], "punct": "smart_zh", "fillers": "strip"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], "準模式聽到的字")
        self.assertEqual(out["redo_of"], first["uid"])
        self.assertNotEqual(out["uid"], first["uid"])
        fast, accurate = self.d.router.calls
        self.assertEqual((fast["model"], accurate["model"]), ("qwen3-asr-1.7b", "whisper-v3"))
        self.assertEqual(fast["n"], accurate["n"], "same audio both times")
        self.assertEqual(accurate["prompt"], "SHORT", "whisper gets the prompt, not hotwords (3 s clip → short prompt)")
        self.assertIsNone(accurate["hotwords"])
        self.assertIsNone(self.d.recent.get(out["uid"]), "a redo keeps no second copy of the clip")
        self.assertIsNotNone(self.d.recent.get(first["uid"]), "the original clip is still there until it expires")
        rows = self._log_rows()
        self.assertEqual(rows[-1]["redo_of"], first["uid"])
        self.assertEqual(rows[-1]["asr_mode"], "accurate")
        self.assertNotIn("redo_of", rows[0])
        self.assertEqual(self.d.results.read()[0]["redo_of"], first["uid"])
        self.assertEqual(self.d.handle({"cmd": "stats"})["ok_count"], 1, "a redo is not a new sentence")

    def test_expired_or_unknown_clip(self):
        _, first = self._say()
        p = self.d.recent.path(first["uid"])
        old = time.time() - 120
        os.utime(p, (old, old))
        self.assertEqual(self.d.handle({"cmd": "retranscribe", "uid": first["uid"]})["error"], "file_not_found")
        self.assertEqual(self.d.handle({"cmd": "retranscribe", "uid": "0123456789ab"})["error"], "file_not_found")
        self.assertEqual(self.d.handle({"cmd": "retranscribe"})["error"], "bad_request")
        self.assertEqual(self.d.handle({"cmd": "retranscribe", "uid": first["uid"], "mode": "slow"})["error"],
                         "bad_request")

    def test_daemon_without_router_cannot_redo(self):
        d = object.__new__(dictated.DictationDaemon)
        d.router = None
        self.assertEqual(d.handle({"cmd": "retranscribe", "uid": "0123456789ab"})["error"], "bad_request")

    def test_errors_are_recorded_too(self):
        out = self.d.handle({"cmd": "transcribe", "wav": str(self.base / "missing.wav")})
        self.assertEqual(out["error"], "file_not_found")
        self.assertNotIn("uid", out)
        row = self.d.results.read()[0]
        self.assertEqual((row["ok"], row["error"], row["wav"]), (False, "file_not_found", str(self.base / "missing.wav")))

    def test_tag_reaches_the_result_file(self):
        _, out = self._say(tag="selftest")
        self.assertEqual(self.d.results.read()[0]["tag"], "selftest")

    def test_ping_tells_the_shell_what_it_can_do(self):
        self.d.router.warm = True
        self.d.router.status = lambda: {}
        with mock.patch.object(dictated.PUNCT_WARMER, "status", return_value={}):
            out = self.d.handle({"cmd": "ping"})
        self.assertEqual(out["recent_audio_s"], 60)
        self.assertEqual(out["results_path"], str(self.base / "last.json"))
        self.d.recent = None
        with mock.patch.object(dictated.PUNCT_WARMER, "status", return_value={}):
            self.assertNotIn("recent_audio_s", self.d.handle({"cmd": "ping"}))


class Version(unittest.TestCase):
    def test_version(self):
        self.assertGreaterEqual(tuple(int(x) for x in dictated.__version__.split(".")), (0, 9, 0))


if __name__ == "__main__":
    unittest.main()
