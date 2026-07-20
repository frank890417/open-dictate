import unittest
from daemon.qa.no_rewrite_gate import content_chars, reachable_by_pairs, assert_no_rewrite


class NoRewriteGateTests(unittest.TestCase):
    def test_punctuation_only_is_allowed(self):
        self.assertEqual(content_chars("哈囉，Open Dictate！"), "哈囉OpenDictate")
        self.assertTrue(reachable_by_pairs("哈囉 Open Dictate", "哈囉，Open Dictate！"))

    def test_numeric_symbols_are_preserved(self):
        self.assertFalse(reachable_by_pairs("折扣是5%", "折扣是5"))
        self.assertFalse(reachable_by_pairs("價格是$100", "價格是100"))

    def test_authorized_pair_is_allowed(self):
        self.assertTrue(reachable_by_pairs("我用阿布西店", "我用Obsidian", [("阿布西店", "Obsidian")]))

    def test_unlisted_rewrite_is_rejected(self):
        self.assertFalse(reachable_by_pairs("今天開會", "明天開會"))
        with self.assertRaises(ValueError):
            assert_no_rewrite("今天開會", "明天開會")


if __name__ == "__main__":
    unittest.main()
