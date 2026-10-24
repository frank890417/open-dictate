"""Deterministic regression tests for daemon v0.8.0: optional filler removal.

Qwen3-ASR writes the hesitation sounds 呃 / 嗯 into the text. With ``"fillers": "strip"`` in the
transcribe request the daemon removes them after punctuation; without it the text is untouched.

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


class StripFillers(unittest.TestCase):
    def test_filler_between_commas_keeps_one_comma(self) -> None:
        self.assertEqual(dictated.strip_fillers("重點應該，呃，可以更強"), ("重點應該，可以更強", 1))
        self.assertEqual(dictated.strip_fillers("目標是，嗯，不是每一個"), ("目標是，不是每一個", 1))

    def test_filler_with_one_comma_goes_with_it(self) -> None:
        self.assertEqual(dictated.strip_fillers("然後呃，我們這邊的東西"), ("然後我們這邊的東西", 1))
        self.assertEqual(dictated.strip_fillers("呃，我覺得可以"), ("我覺得可以", 1))
        self.assertEqual(dictated.strip_fillers("所以可能要呃我直接傳給他"), ("所以可能要我直接傳給他", 1))
        self.assertEqual(dictated.strip_fillers("就這樣，呃。"), ("就這樣。", 1))

    def test_repeated_and_several_fillers(self) -> None:
        self.assertEqual(dictated.strip_fillers("呃呃，這個呃，那個"), ("這個那個", 2))

    def test_leading_hm_is_an_answer_and_stays(self) -> None:
        self.assertEqual(dictated.strip_fillers("嗯，好啊"), ("嗯，好啊", 0))
        self.assertEqual(dictated.strip_fillers("嗯嗯"), ("嗯嗯", 0))
        self.assertEqual(dictated.strip_fillers("嗯，我覺得，嗯，可以"), ("嗯，我覺得，可以", 1))

    def test_text_without_fillers_is_returned_as_is(self) -> None:
        for s in ("今天的會議很順利。", "Hello, world", "  前後空白  "):
            self.assertEqual(dictated.strip_fillers(s), (s, 0))

    def test_only_fillers_leaves_no_content(self) -> None:
        out, n = dictated.strip_fillers("呃。")
        self.assertEqual(n, 1)
        self.assertEqual(dictated._content_chars(out), "")


if __name__ == "__main__":
    unittest.main()
