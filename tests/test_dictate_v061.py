"""Deterministic regression tests for daemon v0.6.1 punctuation fixes.

1. Small-form punctuation (U+FE50-FE57) emitted by large-v3 is a *pause mark*,
   not tone: v0.6.0 mapped it one-to-one, so a whole utterance could come out
   as exclamation marks or enumeration commas.
2. Enumeration comma right before a coordinating conjunction is removed or
   turned into a comma after the punctuation layer.

All inputs are synthetic text/audio; no private log or recording is read.
"""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

import dictated  # type: ignore[import-not-found]  # noqa: E402


def _write_wav(path: Path) -> None:
    samples = [int(30 * math.sin(i * 0.9)) for i in range(4800)]
    samples += [int(3000 * math.sin(2 * math.pi * 220 * i / 16000)) for i in range(11200)]
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))


class SmallFormPunctuation(unittest.TestCase):
    def test_whole_utterance_of_exclamation_small_forms_becomes_pauses(self) -> None:
        # The reported failure shape: every pause came out as "！".
        raw = "有一些版面跑掉了耶﹗你可以幫我用滿視窗嗎﹗就是把預設的視窗拿來看﹗因為預覽的時候太小﹗"
        out = dictated.normalize_small_forms(raw)
        self.assertNotIn("！", out)
        self.assertEqual(out, "有一些版面跑掉了耶，你可以幫我用滿視窗嗎，就是把預設的視窗拿來看，因為預覽的時候太小。")

    def test_enumeration_small_form_is_a_pause_not_a_dunhao(self) -> None:
        out = dictated.normalize_small_forms("然後那個﹑更新網站﹑就是我們的")
        self.assertNotIn("、", out)
        self.assertEqual(out, "然後那個，更新網站，就是我們的")

    def test_trailing_pause_form_becomes_full_stop_and_keeps_trailing_space(self) -> None:
        self.assertEqual(dictated.normalize_small_forms("小snippet﹐ "), "小snippet。 ")
        self.assertEqual(dictated.normalize_small_forms(""), "")
        self.assertEqual(dictated.normalize_small_forms(None), "")

    def test_unambiguous_small_forms_are_unchanged(self) -> None:
        self.assertEqual(dictated.normalize_small_forms("好﹒對﹕是﹔"), "好。對：是；")

    def test_small_question_mark_needs_a_question_word(self) -> None:
        n = dictated.normalize_small_forms
        self.assertEqual(n("這些在哪裡啊﹖可以放到硬碟嗎﹖應該裝得下吧﹖"),
                         "這些在哪裡啊？可以放到硬碟嗎？應該裝得下吧？")
        self.assertEqual(n("我們可以把﹖我就順便跟大家介紹"), "我們可以把，我就順便跟大家介紹")
        self.assertEqual(n("我覺得可以啊﹖我們來看看"), "我覺得可以啊，我們來看看")
        self.assertEqual(n("你是不是忘了﹖好"), "你是不是忘了？好")
        self.assertEqual(n("這要多少錢﹖"), "這要多少錢？")


class DunhaoBeforeConjunction(unittest.TestCase):
    def test_single_dunhao_before_conjunction_is_removed(self) -> None:
        self.assertEqual(dictated.postprocess_punct("把連結、跟相關資訊放進去"), "把連結跟相關資訊放進去")

    def test_clause_connectors_get_a_comma(self) -> None:
        self.assertEqual(dictated.postprocess_punct("視覺層級、跟字體顏色、還有它的架構"),
                         "視覺層級跟字體顏色，還有它的架構")

    def test_chained_conjunctions_become_commas_sentence_by_sentence(self) -> None:
        self.assertEqual(dictated.postprocess_punct("我去上打鼓課、或是鋼琴課、或是學吉他。然後A、跟B"),
                         "我去上打鼓課，或是鋼琴課，或是學吉他。然後A跟B")
        self.assertEqual(dictated.postprocess_punct("我要跟你、跟他說"), "我要跟你，跟他說")

    def test_real_enumerations_and_lookalike_words_are_kept(self) -> None:
        for keep in ("GEO、SEO", "日期、時間參數", "A、或許B", "A、及時B", "UI、UX"):
            self.assertEqual(dictated.postprocess_punct(keep), keep)


class TranscribePath(unittest.TestCase):
    def _run(self, asr_text: str, mode: str) -> dict:
        class FakeLexicon:
            contextual: list = []
            replacements: list = []

            @staticmethod
            def correct(text):
                return text, []

        fake_mlx = types.ModuleType("mlx_whisper")
        setattr(fake_mlx, "transcribe", lambda *_a, **_k: {
            "text": asr_text, "segments": [{"text": asr_text, "no_speech_prob": 0.0}]})
        daemon = object.__new__(dictated.DictationDaemon)
        daemon.lex = FakeLexicon()
        daemon.initial_prompt = "synthetic prompt"
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            _write_wav(wav)
            with mock.patch.dict(sys.modules, {"mlx_whisper": fake_mlx}), \
                    mock.patch.object(dictated, "LOG_DIR", Path(tmp) / "log"):
                return daemon.handle_transcribe({"wav": str(wav), "punct": mode})

    def test_pause_marks_and_dunhao_through_transcribe(self) -> None:
        asr = "幫我整理一下﹗然後把連結、跟相關資訊放到清單﹗我明天來弄。"
        smart = self._run(asr, "smart_zh")
        self.assertEqual(smart["raw"], asr, "raw 欄位永遠是 ASR 原樣")
        self.assertEqual(smart["text"], "幫我整理一下，然後把連結跟相關資訊放到清單，我明天來弄。")
        self.assertNotIn("！", smart["text"])
        raw = self._run(asr, "raw")
        self.assertEqual(raw["text"], "幫我整理一下，然後把連結、跟相關資訊放到清單，我明天來弄。",
                         "raw 模式只做小寫形正規化，不套連接詞收尾")

    def test_version_is_0_6_1(self) -> None:
        self.assertEqual(dictated.__version__, "0.6.1")


if __name__ == "__main__":
    unittest.main()
