"""The public-safety scan must keep catching real secrets and private paths, and must not
fail on code that merely reads a secret from the macOS keychain (daemon 0.7.0 does that).

All strings below are made up. Those that must trip the scan are assembled at run time, so
this file does not trip the scan itself.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN, KEY, PASSWORD, USERS = "to" + "ken", "API" + "_KEY", "pass" + "word", "/Us" + "ers"
SCAN = ROOT / "scripts" / "public-safety-scan.py"


def load():
    spec = importlib.util.spec_from_file_location("public_safety_scan", SCAN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LineRules(unittest.TestCase):
    def setUp(self):
        self.scan = load()
        self.rules = [re.compile(p, re.I) for p in self.scan.BLOCK_PATTERNS]

    def hit(self, line: str) -> bool:
        return bool(self.scan.line_hits(line, self.rules))

    def test_reading_from_the_keychain_is_not_a_secret(self):
        # the two lines that failed CI's scan before the exception existed
        self.assertFalse(self.hit("            token = asr_router.keychain_secret(ASR_REMOTE_KEYCHAIN) if ASR_REMOTE_KEYCHAIN else None"))
        self.assertFalse(self.hit('    token = keychain_secret(args.token_keychain) if args.token_keychain else os.environ.get(args.token_env, "")'))

    def test_literal_secrets_are_still_caught(self):
        self.assertTrue(self.hit(f'{TOKEN} = "abcdefghijklmnop1234"'))
        self.assertTrue(self.hit(f"{KEY}: abcdef1234567890xyz"))
        self.assertTrue(self.hit(f"{PASSWORD} = 'hunter2hunter2hunter2'"))

    def test_the_exception_covers_one_rule_only(self):
        self.assertTrue(self.hit('token = keychain_secret("svc") or "ghp_' + "A" * 12 + '"'))
        self.assertTrue(self.hit(f"{TOKEN} = keychain_secret('svc')  # {USERS}/somebody/key"))

    def test_private_paths_are_still_caught(self):
        self.assertTrue(self.hit(f"python = '{USERS}/somebody/venv/bin/python'"))


class WholeTree(unittest.TestCase):
    def test_the_repository_passes(self):
        r = subprocess.run([sys.executable, str(SCAN)], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
