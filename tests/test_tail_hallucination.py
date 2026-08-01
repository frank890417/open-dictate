"""迴圈幻覺尾巴層的回歸測試（daemon v0.5.3）。

Whisper 在極短與極長音訊兩端都會陷入 token 迴圈，把同一個字或片語重複到視窗結束。
這層負責砍掉貼著結尾的那一段；核心風險不是「漏砍」，是「錯砍真話」，
所以下面的保守性案例跟失控案例一樣重要。

⚠️ 全部使用合成語料，不含任何真實聽寫內容。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

_DAEMON_DIR = Path(__file__).resolve().parents[1] / "daemon"
# dictated.py 以腳本模式執行時是 `from product_config import ...`（非相對匯入），
# 所以直接載檔前要先讓 daemon/ 進 sys.path。
if str(_DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(_DAEMON_DIR))

# 只需要純函式，直接載模組即可（不啟動 socket、不載模型）。
_SPEC = importlib.util.spec_from_file_location("dictated_for_test", _DAEMON_DIR / "dictated.py")
dictated = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dictated)
strip = dictated.strip_tail_hallucination


class TestTailHallucination(unittest.TestCase):
    # ---------------------------------------------------------------- 該砍的
    def test_single_char_runaway_tail(self):
        """真內容 + 單字重複到視窗結束 → 砍掉尾巴，保留內容。"""
        out, why = strip("我想要做一個個人的儀表板然後幫我盤點所有的分頁" + "多" * 120)
        self.assertEqual(out, "我想要做一個個人的儀表板然後幫我盤點所有的分頁")
        self.assertIn("tail_loop", why)

    def test_four_repeats_are_enough_when_body_exists(self):
        """只重複四次也要接住——這是舊版門檻（六次）漏掉的那一類。"""
        out, _ = strip("讓工具當成輔助來快速開發過去要計算很久的東西安安安安")
        self.assertEqual(out, "讓工具當成輔助來快速開發過去要計算很久的東西")

    def test_multi_char_phrase_loop(self):
        """長片語迴圈：token 上限若沿用 12 會整段漏掉。"""
        src = "對不是說那個東西" + "好，我們來看看這個演出的表演吧。" * 4
        out, _ = strip(src)
        self.assertEqual(out, "對不是說那個東西")

    def test_latin_token_loop(self):
        out, _ = strip("把它放到心裡面" + "four" * 5)
        self.assertEqual(out, "把它放到心裡面")

    def test_whole_utterance_is_hallucination(self):
        """整句都是迴圈、前面沒有內容 → 空字串，呼叫端應判 no_speech。"""
        out, why = strip("多" * 221)
        self.assertEqual(out, "")
        self.assertIn("all_loop", why)

    def test_short_body_uses_higher_threshold(self):
        """body 很短時門檻拉高到六次，但仍保留 body 而不是回空。"""
        out, _ = strip("他一直說對對對對對對")
        self.assertEqual(out, "他一直說")

    # ------------------------------------------------------------ 絕不能砍的
    def test_natural_emphasis_untouched(self):
        for src in ("加油加油加油", "加油加油加油加油", "對對對對", "嗯嗯嗯", "好啦好啦"):
            with self.subTest(src=src):
                out, why = strip(src)
                self.assertEqual(out, src)
                self.assertIsNone(why)

    def test_mid_sentence_repetition_untouched(self):
        """重複在句中而不是結尾 → 這層不管，交給 lexicon 的保守門檻。"""
        src = "這個真的真的很重要，我再說一次"
        self.assertEqual(strip(src)[0], src)

    def test_normal_sentence_untouched(self):
        src = "我覺得最近語音辨識的狀況運作的不是非常理想"
        self.assertEqual(strip(src)[0], src)

    def test_punctuation_only_repetition_untouched(self):
        """純標點重複不是幻覺訊號，不碰。"""
        src = "他停頓了很久......"
        self.assertEqual(strip(src)[0], src)

    def test_empty_and_none_safe(self):
        self.assertEqual(strip("")[0], "")
        self.assertEqual(strip(None)[0], None)


class TestPunctTimeoutBudget(unittest.TestCase):
    def test_budget_grows_with_length_then_caps(self):
        f = dictated.punct_timeout_for
        self.assertLess(f(20), f(150))
        self.assertLess(f(150), f(300))
        self.assertEqual(f(2000), dictated.PUNCT_LLM_TIMEOUT_CAP_S)

    def test_cap_stays_within_shell_headroom(self):
        """CAP 與殼的 llmHeadroom 是一組契約：破約會造成假離線。"""
        self.assertLessEqual(dictated.PUNCT_LLM_TIMEOUT_CAP_S, 10.0)


if __name__ == "__main__":
    unittest.main()
