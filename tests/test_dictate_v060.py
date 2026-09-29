"""Deterministic regression tests for daemon v0.5.4–v0.6.0.

Covers: the process-local punctuation circuit breaker, the LLM warm keeper
(cold model ⇒ immediate rule-layer fallback, no load cancellation), the
shell-deadline guard, ASR decode guards, the false-trigger gate, the
subtitle-credit hallucination filter, prompt-anomaly retry and the TAIL_LOOP
catastrophic-backtracking fix.

⚠️ 全部使用合成文字／合成音訊，不讀任何私人聽寫 log 或真實錄音，也不連 Ollama。
"""

from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
os.environ.setdefault("OPEN_DICTATE_PUNCT_COOLDOWN_S", "30")
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

import dictated  # type: ignore[import-not-found]  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


def _response(text: str) -> _Response:
    payload = {"message": {"content": text}}
    return _Response(json.dumps(payload).encode("utf-8"))


def _write_synthetic_wav(path: Path) -> None:
    # 1 second of synthetic PCM16; no private recording is involved.
    # v0.6.0 誤觸閘門看「有沒有起伏」：前 0.3 秒低底噪、後 0.7 秒 220Hz 正弦，模擬講話的形狀。
    import math
    samples = [int(30 * math.sin(i * 0.9)) for i in range(4800)]
    samples += [int(3000 * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(11200)]
    frames = struct.pack("<" + "h" * len(samples), *samples)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(frames)


class PunctCircuitRegression(unittest.TestCase):
    def setUp(self) -> None:
        self._old_cooldown = dictated.PUNCT_COOLDOWN_S
        self._old_until = dictated._punct_circuit_open_until
        self._old_notice_until = dictated._punct_circuit_notice_until
        dictated.PUNCT_COOLDOWN_S = 30.0
        dictated._punct_circuit_open_until = 0.0
        dictated._punct_circuit_notice_until = 0.0
        # v0.6.0：這組測的是「模型在記憶體裡」之後的斷路器行為 → 先標記就緒
        dictated.PUNCT_WARMER.mark_ready()

    def tearDown(self) -> None:
        dictated.PUNCT_COOLDOWN_S = self._old_cooldown
        dictated._punct_circuit_open_until = self._old_until
        dictated._punct_circuit_notice_until = self._old_notice_until
        dictated.PUNCT_WARMER.ready_at = 0.0

    def test_timeout_skips_urllib_during_cooldown_and_uses_existing_fallback(self) -> None:
        calls = 0

        def timeout(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise TimeoutError("synthetic timeout")

        source = "synthetic，input"
        expected = dictated.smart_punct_zh(source)
        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=timeout), \
                mock.patch.object(dictated, "_machine_load", return_value="synthetic-load"), \
                mock.patch.object(dictated, "log_line"):
            first, first_mode = dictated.llm_punct_routed(source, [], [])
            second, second_mode = dictated.llm_punct_routed(source, [], [])

        self.assertEqual(calls, 1, "the second sentence must not call urllib during cooldown")
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertEqual(first_mode, "smart_zh_fallback")
        self.assertEqual(second_mode, "smart_zh_fallback")
        self.assertGreater(dictated._punct_circuit_open_until, 0.0)

    def test_cooldown_expiry_allows_llm_recovery(self) -> None:
        dictated._punct_circuit_open_until = time.monotonic() - 1.0
        calls = 0

        def recovered(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return _response("recovered input")

        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=recovered), \
                mock.patch.object(dictated, "log_line"):
            output, mode = dictated.llm_punct_routed("recovered input", [], [])

        self.assertEqual(calls, 1)
        self.assertEqual(output, "recovered input")
        self.assertEqual(mode, "llm_zh")
        self.assertEqual(dictated._punct_circuit_remaining(), 0.0)

    def test_gate_failure_does_not_open_circuit_and_next_call_retries(self) -> None:
        responses = [_response("gate altered"), _response("gate input")]
        calls = 0

        def fake_urlopen(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return responses.pop(0)

        source = "gate input"
        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(dictated, "log_line"):
            fallback, fallback_mode = dictated.llm_punct_routed(source, [], [])
            self.assertEqual(dictated._punct_circuit_remaining(), 0.0)
            recovered, recovered_mode = dictated.llm_punct_routed(source, [], [])

        self.assertEqual(calls, 2, "a gate rejection must not suppress the next LLM attempt")
        self.assertEqual(fallback, dictated.smart_punct_zh(source))
        self.assertEqual(fallback_mode, "smart_zh_fallback")
        self.assertEqual(recovered, source)
        self.assertEqual(recovered_mode, "llm_zh")

    def test_cooldown_env_default_and_override(self) -> None:
        env = os.environ.copy()
        env.pop("OPEN_DICTATE_PUNCT_COOLDOWN_S", None)
        env.pop("OPEN_DICTATE_PRODUCT_ENV_PREFIX", None)
        env["PYTHONPATH"] = str(DAEMON_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        probe = [sys.executable, "-c", "import dictated; print(dictated.PUNCT_COOLDOWN_S)"]

        default = subprocess.run(probe, cwd=ROOT, env=env, check=True,
                                 capture_output=True, text=True)
        self.assertEqual(default.stdout.strip(), "30.0")

        env["OPEN_DICTATE_PUNCT_COOLDOWN_S"] = "7.5"
        override = subprocess.run(probe, cwd=ROOT, env=env, check=True,
                                  capture_output=True, text=True)
        self.assertEqual(override.stdout.strip(), "7.5")

    def test_success_and_failure_logs_keep_old_schema_and_add_stage_timings(self) -> None:
        class FakeLexicon:
            contextual = []
            replacements = []

            @staticmethod
            def correct(text):
                return text, []

        state = {"fail": False}
        fake_mlx = types.ModuleType("mlx_whisper")

        def transcribe(*_args, **_kwargs):
            if state["fail"]:
                raise RuntimeError("synthetic ASR failure")
            return {
                "text": "synthetic input",
                "segments": [{"text": "synthetic input", "no_speech_prob": 0.0}],
            }

        setattr(fake_mlx, "transcribe", transcribe)
        daemon = object.__new__(dictated.DictationDaemon)
        daemon.lex = FakeLexicon()
        daemon.initial_prompt = "synthetic prompt"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_dir = tmp_path / "log"
            first_wav = tmp_path / "first.wav"
            second_wav = tmp_path / "second.wav"
            _write_synthetic_wav(first_wav)
            _write_synthetic_wav(second_wav)

            with mock.patch.dict(sys.modules, {"mlx_whisper": fake_mlx}), \
                    mock.patch.object(dictated, "LOG_DIR", log_dir):
                success = daemon.handle_transcribe({"wav": str(first_wav), "punct": "smart_zh"})
                state["fail"] = True
                failure = daemon.handle_transcribe({"wav": str(second_wav), "punct": "smart_zh"})

            log_files = list(log_dir.glob("*.jsonl"))
            self.assertEqual(len(log_files), 1)
            rows = [json.loads(line) for line in log_files[0].read_text(encoding="utf-8").splitlines()]

        self.assertTrue(success["ok"])
        self.assertEqual(failure["error"], "asr_failed")
        self.assertEqual(len(rows), 2)
        old_fields = {"ts", "wav_dur_s", "raw", "text", "changes", "asr_ms", "total_ms"}
        stage_fields = {"pre_asr_ms", "load_ms", "asr_ms", "lex_ms", "punct_ms"}
        for row in rows:
            self.assertTrue(old_fields.issubset(row))
            self.assertTrue(stage_fields.issubset(row))
            for field in stage_fields:
                self.assertIsInstance(row[field], int)
                self.assertGreaterEqual(row[field], 0)
        for field in ("pre_asr_ms", "load_ms", "asr_ms", "lex_ms", "punct_ms"):
            self.assertIn(field, success)


class ColdModelAndDeadline(unittest.TestCase):
    """v0.6.0：模型不在記憶體 → 不打 chat（打了只會把載入取消掉）；殼逾時快到 → 不打。"""

    def setUp(self) -> None:
        dictated._punct_circuit_open_until = 0.0
        dictated._punct_circuit_notice_until = 0.0
        dictated.PUNCT_WARMER.ready_at = 0.0
        dictated.PUNCT_WARMER.loading = False

    def tearDown(self) -> None:
        dictated.PUNCT_WARMER.ready_at = 0.0
        dictated.PUNCT_WARMER.loading = False

    def test_cold_model_skips_chat_and_kicks_warmer(self) -> None:
        urls = []

        def fake_urlopen(req, *_args, **_kwargs):
            url = req if isinstance(req, str) else req.full_url
            urls.append(url)
            if url.endswith("/api/ps"):
                return _Response(json.dumps({"models": []}).encode("utf-8"))
            raise AssertionError("chat must not be called while the model is cold")

        source = "cold model input"
        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(dictated.PUNCT_WARMER, "kick") as kick, \
                mock.patch.object(dictated, "log_line"):
            t0 = time.perf_counter()
            out, mode = dictated.llm_punct_routed(source, [], [])
            spent = time.perf_counter() - t0

        self.assertEqual(mode, "smart_zh_fallback")
        self.assertEqual(out, dictated.smart_punct_zh(source))
        self.assertTrue(all(u.endswith("/api/ps") for u in urls))
        self.assertTrue(kick.called)
        self.assertLess(spent, 0.5, "cold path must fall back immediately, not wait a budget")
        self.assertEqual(dictated._punct_circuit_remaining(), 0.0, "cold ≠ broken: no circuit trip")

    def test_loaded_model_is_detected_via_ps(self) -> None:
        name = dictated.PUNCT_LLM_MODEL

        def fake_urlopen(req, *_args, **_kwargs):
            url = req if isinstance(req, str) else req.full_url
            if url.endswith("/api/ps"):
                return _Response(json.dumps({"models": [{"name": name, "model": name}]}).encode("utf-8"))
            return _response("loaded input")

        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(dictated, "log_line"):
            out, mode = dictated.llm_punct_routed("loaded input", [], [])
        self.assertEqual((out, mode), ("loaded input", "llm_zh"))

    def test_deadline_too_close_skips_llm(self) -> None:
        dictated.PUNCT_WARMER.mark_ready()
        with mock.patch.object(dictated.urllib.request, "urlopen",
                               side_effect=AssertionError("no LLM call past the deadline")), \
                mock.patch.object(dictated, "log_line"):
            out, mode = dictated.llm_punct_routed("late input", [], [],
                                                  deadline=time.monotonic() + 0.2)
        self.assertEqual(mode, "smart_zh_fallback")

    def test_deadline_caps_the_request_budget(self) -> None:
        dictated.PUNCT_WARMER.mark_ready()
        seen = {}

        def fake_urlopen(req, *_args, timeout=None, **_kwargs):
            seen["timeout"] = timeout
            return _response("capped input")

        with mock.patch.object(dictated.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(dictated, "log_line"):
            dictated.llm_punct_routed("capped input", [], [], deadline=time.monotonic() + 1.0)
        self.assertLessEqual(seen["timeout"], 1.0)


class AsrGuards(unittest.TestCase):
    """v0.6.0：解碼護欄、已知署名幻覺、迴圈殘渣。純字串，無音檔、無模型。"""

    def test_decode_options_scale_with_duration(self) -> None:
        short = dictated.asr_decode_options(1.5)
        self.assertLessEqual(short["sample_len"], 60)
        self.assertEqual(short["temperature"], dictated.ASR_SHORT_TEMPS)
        long = dictated.asr_decode_options(60)
        self.assertEqual(long["sample_len"], 224)
        self.assertLessEqual(max(long["temperature"]), 0.4, "高溫輪次只產垃圾，階梯截在 0.4")
        self.assertEqual(dictated.asr_decode_options(None), {})

    def test_small_forms_are_normalized(self) -> None:
        self.assertEqual(dictated.normalize_small_forms("好﹐我知道﹖對﹗"), "好，我知道？對！")

    def test_anomaly_needs_voiced_long_audio(self) -> None:
        self.assertTrue(dictated.asr_output_anomalous("整個", 30.0, 0.6))
        self.assertTrue(dictated.asr_output_anomalous("", 12.0, 0.6))
        self.assertFalse(dictated.asr_output_anomalous("整個", 3.0, 0.6), "短錄音不重試")
        self.assertFalse(dictated.asr_output_anomalous("", 30.0, 0.1), "沒有講話形狀不重試")
        self.assertFalse(dictated.asr_output_anomalous("我覺得這個作品可以再調一下" * 3, 8.0, 0.6))

    def test_retry_with_other_prompt_rescues_collapsed_output(self) -> None:
        import numpy as np
        calls = []

        class FakeMlx:
            @staticmethod
            def transcribe(_src, initial_prompt=None, **_kw):
                calls.append(initial_prompt)
                if len(calls) == 1:
                    return {"text": "4分" * 30, "segments": [{"text": "4分" * 30, "no_speech_prob": 0.0}]}
                good = "我們先看這個作品的聲音然後再討論燈光怎麼調整比較好"
                return {"text": good, "segments": [{"text": good, "no_speech_prob": 0.0}]}

        class FakeLexicon:
            contextual = []
            replacements = []

            @staticmethod
            def correct(text):
                return text, []

        daemon = object.__new__(dictated.DictationDaemon)
        daemon.lex = FakeLexicon()
        daemon.initial_prompt = "LONG"
        daemon.initial_prompt_short = "SHORT"
        rng = np.random.default_rng(1)
        t = np.arange(16000 * 8) / 16000
        audio = (np.sin(2 * np.pi * 200 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 1.5 * t)) * 0.2
                 + rng.standard_normal(t.size) * 0.001).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "speechy.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes((audio * 32767).astype(np.int16).tobytes())
            with mock.patch.dict(sys.modules, {"mlx_whisper": FakeMlx}), \
                    mock.patch.object(dictated, "LOG_DIR", Path(tmp) / "log"), \
                    mock.patch.object(dictated, "log_line"):
                out = daemon.handle_transcribe({"wav": str(wav), "punct": "smart_zh"})
            rows = [json.loads(x) for x in next((Path(tmp) / "log").glob("*.jsonl")).read_text().splitlines()]
        self.assertEqual(calls, ["LONG", "SHORT"])
        self.assertTrue(out["ok"])
        self.assertIn("作品", out["text"])
        self.assertEqual(rows[-1]["asr_retry"], "short_prompt")
        self.assertIn("4分", rows[-1]["raw_first"])

    def test_known_hallucinations_become_no_speech(self) -> None:
        for s in ("MING PAO CANADA // MING PAO TORONTO", "詞曲 李宗盛", "字幕由 Amara.org 社群提供",
                  "字幕志愿者 李宗盛", "本歌曲来自〖云上工作室〗", "。", "本集完"):
            self.assertEqual(dictated.strip_known_hallucination(s)[0], "", s)

    def test_known_hallucination_tail_is_trimmed_only_when_unambiguous(self) -> None:
        out, why = dictated.strip_known_hallucination("今天聊這個作品。字幕由Amara.org社群提供")
        self.assertEqual(out, "今天聊這個作品。")
        self.assertIn("tail", why)
        for keep in ("我很喜歡李宗盛的歌", "這首歌詞曲李宗盛", "我覺得MING PAO那篇報導寫得不錯",
                     "好的我知道了", "本集完成了嗎"):
            self.assertEqual(dictated.strip_known_hallucination(keep), (keep, None), keep)

    def test_tail_loop_has_no_catastrophic_backtracking(self) -> None:
        # 2026-09-29：「整個＋40 個空白／逗號＋一個字」讓舊 TAIL_LOOP 回溯 >20 秒（daemon 單執行緒＝聽寫全停）
        t0 = time.perf_counter()
        for bomb in ("整個" + " " * 300 + "好", "整個" + "，" * 300 + "好", "整個" + "！" * 300 + "好",
                     "好，" * 1500 + "X"):
            dictated.strip_tail_hallucination(bomb)
        self.assertLess(time.perf_counter() - t0, 3.0)
        # 壓分隔符只用來比對，切點映射回原文：body 裡的省略號、空白原樣保留
        out, why = dictated.strip_tail_hallucination("我覺得……這樣 可以" + "好好好好好好好好好")
        self.assertEqual(out, "我覺得……這樣 可以")

    def test_loop_with_junk_body_is_no_speech(self) -> None:
        self.assertEqual(dictated.strip_tail_hallucination("4.4 " + "on " * 16)[0], "")
        for keep in ("加油加油加油", "對對對對", "嗯嗯嗯"):
            self.assertEqual(dictated.strip_tail_hallucination(keep)[0], keep)

    def test_speech_gate_blocks_flat_noise_but_not_bursts(self) -> None:
        import numpy as np
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(16000) * 0.002).astype(np.float32)       # 1 秒房間底噪
        hit, dr, voiced = dictated.looks_like_no_speech(noise, 1.0)
        self.assertTrue(hit, (dr, voiced))
        burst = noise.copy()
        burst[4000:12000] += (np.sin(np.arange(8000) * 2 * np.pi * 180 / 16000) * 0.1).astype(np.float32)
        self.assertFalse(dictated.looks_like_no_speech(burst, 1.0)[0])
        # 長錄音永遠進 ASR（閘門只管 <4 秒的誤觸）
        long_noise = (rng.standard_normal(16000 * 6) * 0.002).astype(np.float32)
        self.assertFalse(dictated.looks_like_no_speech(long_noise, 6.0)[0])

    def test_trim_silence_cuts_only_clear_edges(self) -> None:
        import numpy as np
        rng = np.random.default_rng(2)
        sr = 16000
        floor = lambda sec: (rng.standard_normal(int(sr * sec)) * 0.002).astype(np.float32)
        t = np.arange(int(sr * 2.0)) / sr
        tone = (np.sin(2 * np.pi * 180 * t) * 0.1).astype(np.float32) + floor(2.0)
        audio = np.concatenate([floor(1.0), tone, floor(1.5)])
        out, head, tail = dictated.trim_silence(audio)
        self.assertAlmostEqual(head, 0.7, delta=0.05)
        self.assertAlmostEqual(tail, 1.0, delta=0.05)
        self.assertGreaterEqual(len(out) / sr, 2.0)
        # 短於 0.6 秒的頭尾靜音不動；沒有安靜底（連續講話）不動
        self.assertEqual(dictated.trim_silence(np.concatenate([floor(0.4), tone, floor(0.4)]))[1:], (0.0, 0.0))
        self.assertEqual(dictated.trim_silence(tone)[1:], (0.0, 0.0))

    def test_shell_deadline_matches_contract(self) -> None:
        # IO-CONTRACT：殼 timeout = max(15, 秒/6 + 12)，daemon 扣 2 秒安全邊際
        self.assertAlmostEqual(dictated.shell_deadline_s(5), 13.0)
        self.assertAlmostEqual(dictated.shell_deadline_s(120), 30.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
