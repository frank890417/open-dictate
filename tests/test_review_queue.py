import os
import tempfile
import unittest
from pathlib import Path
from daemon.glossary.review_queue import ReviewQueue


class ReviewQueueTests(unittest.TestCase):
    def test_accept_reject_undo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            q = ReviewQueue(root / "queue.json", root / "personal.json")
            row = q.add("阿布西店", "Obsidian", source="test")
            self.assertEqual(row["status"], "pending")
            accepted = q.accept(row["id"])
            self.assertEqual(accepted["status"], "accepted")
            self.assertIn("Obsidian", (root / "personal.json").read_text(encoding="utf-8"))
            undone = q.undo(row["id"])
            self.assertEqual(undone["status"], "pending")
            rejected = q.reject(row["id"], reason="too risky")
            self.assertEqual(rejected["status"], "rejected")
            self.assertEqual(oct(os.stat(root / "queue.json").st_mode & 0o777), "0o600")
            self.assertEqual(oct(os.stat(root / "personal.json").st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
