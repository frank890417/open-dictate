import unittest
from daemon.qa.mishear_detector import scan_text


class MishearDetectorTests(unittest.TestCase):
    def test_flags_near_canonical_term(self):
        flags = scan_text("我今天打開阿布西店整理筆記", canonical=["Obsidian"])
        self.assertTrue(any(f.type == "possible_mishear" and f.candidate == "Obsidian" for f in flags))

    def test_flags_numbers_for_review(self):
        flags = scan_text("預算是3000元，日期是7月20日", canonical=[])
        self.assertGreaterEqual(len([f for f in flags if f.type == "number_review"]), 2)


if __name__ == "__main__":
    unittest.main()
