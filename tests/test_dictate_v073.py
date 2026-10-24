"""Deterministic regression tests for daemon v0.7.3: hotword list read back on a silent tap.

Qwen3-ASR, given an almost silent 0.5-2.5 s recording (the key press passes the speech gate),
returns its own hotword list in order instead of no_speech. The daemon now recognises a whole
utterance that is that list and treats it as no_speech; ordinary speech that names a few of
the same terms is kept.

All inputs are synthetic text; no private log or recording is read.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAEMON_DIR = ROOT / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

# The suite must never reach a real Ollama (the warm keeper would load a 21 GB model on a developer's Mac): port 9 refuses at once.
os.environ.setdefault("OPEN_DICTATE_PUNCT_LLM_URL", "http://127.0.0.1:9/api/chat")
import dictated  # type: ignore[import-not-found]  # noqa: E402

TERMS = ["王小明", "小明", "Alice", "Open Dictate", "README.md", "TouchDesigner", "p5.js",
         "鬥陣", "駐村", "徵件", "Obsidian", "Notion", "launchd", "Ollama", "北美館", "萬物公式",
         "靈魂魚", "演算詩篇", "咖啡幻夢"]
INDEX = dictated.prompt_echo_index(TERMS)


class PromptEcho(unittest.TestCase):
    def test_whole_list_read_back_is_echo(self) -> None:
        for s in (", ".join(TERMS[:14]) + ", 北",                     # half-width commas, cut mid-term
                  "，".join(TERMS[:12]) + "，",                        # full-width, trailing separator
                  ", ".join(TERMS[:9]) + ", �",                   # broken last byte
                  ", ".join(TERMS)):
            self.assertTrue(dictated.is_prompt_echo(s, INDEX), s)

    @unittest.skipUnless(dictated.apply_opencc("驻") == "駐", "needs OpenCC to fold simplified terms")
    def test_simplified_read_back_is_echo(self) -> None:
        s = "王小明，小明，Alice，Open Dictate，README.md，TouchDesigner，p5.js，斗阵，驻村"
        self.assertTrue(dictated.is_prompt_echo(s, INDEX), s)

    def test_speech_that_names_some_terms_is_kept(self) -> None:
        for s in ("作品的話，萬物公式、靈魂魚、演算詩篇、咖啡幻夢就剛好整個講過去，然後抓二十分鐘左右",
                  "Obsidian、Notion、launchd、Ollama、北美館",                 # five in order, under the floor
                  "今天用 TouchDesigner 和 p5.js 做每日創作，晚上在 Obsidian 整理筆記，然後把 README.md 寫完",
                  "Alice，Alice，Alice，Alice，Alice，Alice",                   # repeated, not in list order
                  "小明，王小明，Alice，Obsidian，鬥陣，Notion，徵件"):           # out of order
            self.assertFalse(dictated.is_prompt_echo(s, INDEX), s)

    def test_no_index_never_matches(self) -> None:
        self.assertFalse(dictated.is_prompt_echo(", ".join(TERMS), None))
        self.assertFalse(dictated.is_prompt_echo(", ".join(TERMS), {}))

    def test_clean_asr_turns_echo_into_no_speech(self) -> None:
        echo = {"text": ", ".join(TERMS[:15]), "segments": [{"no_speech_prob": 0.0, "text": "x"}]}
        self.assertEqual(dictated.DictationDaemon._clean_asr(echo, INDEX), ("", "prompt_echo"))
        self.assertEqual(dictated.DictationDaemon._clean_asr(echo)[1], None)       # old callers: unchanged
        speech = {"text": "我在 Obsidian 整理筆記", "segments": [{"no_speech_prob": 0.0, "text": "x"}]}
        self.assertEqual(dictated.DictationDaemon._clean_asr(speech, INDEX), ("我在 Obsidian 整理筆記", None))


if __name__ == "__main__":
    unittest.main()
