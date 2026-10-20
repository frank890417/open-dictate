"""./install.sh and scripts/install-recognition.py: the default install turns the fast recognition
mode on, says what it will download BEFORE downloading, and offers `accurate` and `off`.

Nothing here installs or downloads anything: `install.sh --plan` stops before any change, the
helper runs against an empty temporary Hugging Face cache, and the one test that would need the
network only checks that `--yes` is demanded.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
HELPER = ROOT / "scripts" / "install-recognition.py"
sys.path.insert(0, str(ROOT / "daemon"))


def load_helper():
    spec = importlib.util.spec_from_file_location("install_recognition", HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Env:
    """A throw-away HOME and Hugging Face cache, so the machine's own state never leaks in."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.cache = Path(self.tmp.name) / "hf" / "hub"
        self.home.mkdir()
        self.cache.mkdir(parents=True)
        self.env = {**os.environ, "HOME": str(self.home), "HF_HOME": str(self.cache.parent)}
        for k in ("HF_HUB_CACHE", "XDG_CACHE_HOME", "OPEN_DICTATE_DATA_ROOT", "OPEN_DICTATE_ASR_MODE_STATE"):
            self.env.pop(k, None)
        return self

    def __exit__(self, *exc):
        self.tmp.cleanup()

    def cache_model(self, repo: str, weights: str, size: int | None = None) -> None:
        """A finished download: the weight file is a sparse file of the size the helper expects."""
        h = load_helper()
        size = h.KNOWN[repo]["bytes"] if size is None else size
        snap = self.cache / ("models--" + repo.replace("/", "--")) / "snapshots" / ("a" * 40)
        snap.mkdir(parents=True, exist_ok=True)
        with open(snap / weights, "wb") as f:
            f.truncate(size)
        (snap / "config.json").write_text("{}")


def run(args, env, stdin=subprocess.DEVNULL):
    r = subprocess.run(args, capture_output=True, env=env, stdin=stdin, cwd=ROOT, timeout=60)
    return subprocess.CompletedProcess(r.args, r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"))


class Helper(unittest.TestCase):
    def test_repos_match_what_the_daemon_really_loads(self):
        # the helper cannot import the daemon (no numpy before the venv exists), so this test does
        h = load_helper()
        import asr_engine
        self.assertEqual(h.FAST_REPO, asr_engine.MODELS["qwen3-asr-1.7b"]["repo"])
        src = (ROOT / "daemon" / "dictated.py").read_text(encoding="utf-8")
        self.assertIn(f'MODEL = env("MODEL", "{h.ACCURATE_REPO}")', src)
        self.assertIn(f'ASR_FAST_MODEL = env("ASR_FAST_MODEL", "qwen3-asr-1.7b")', src)

    def test_fast_and_accurate_need_both_models_and_off_needs_one(self):
        h = load_helper()
        with Env() as e:
            os.environ["HF_HOME"] = str(e.cache.parent)
            try:
                fast, acc, off = h.plan("fast", e.cache), h.plan("accurate", e.cache), h.plan("off", e.cache)
            finally:
                os.environ.pop("HF_HOME", None)
        self.assertEqual([m["repo"] for m in fast["models"]], [h.FAST_REPO, h.ACCURATE_REPO])
        self.assertEqual([m["repo"] for m in acc["models"]], [h.FAST_REPO, h.ACCURATE_REPO])
        self.assertEqual([m["repo"] for m in off["models"]], [h.ACCURATE_REPO])
        self.assertEqual(fast["missing_bytes"], 2_470_000_000 + 1_610_000_000)
        self.assertEqual(off["missing_bytes"], 1_610_000_000)
        self.assertTrue(fast["needs_download"])

    def test_a_cached_model_is_not_downloaded_again(self):
        h = load_helper()
        with Env() as e:
            e.cache_model(h.FAST_REPO, "model.safetensors")
            self.assertTrue(h.is_cached(h.FAST_REPO, e.cache))
            self.assertFalse(h.is_cached(h.ACCURATE_REPO, e.cache))
            self.assertEqual(h.plan("fast", e.cache)["missing_bytes"], 1_610_000_000)
            e.cache_model(h.ACCURATE_REPO, "weights.safetensors")
            self.assertFalse(h.plan("fast", e.cache)["needs_download"])

    def test_a_stale_incomplete_blob_does_not_hide_a_finished_download(self):
        # Found by killing a download and running it again: huggingface_hub finishes the model but
        # leaves the old `*.incomplete` blob behind. The first version of is_cached() treated that
        # blob as "half-downloaded", so the installer declared a good model missing and failed.
        h = load_helper()
        with Env() as e:
            e.cache_model(h.ACCURATE_REPO, "weights.safetensors")
            blobs = e.cache / ("models--" + h.ACCURATE_REPO.replace("/", "--")) / "blobs"
            blobs.mkdir()
            (blobs / "deadbeef.7e86ee6e.incomplete").write_bytes(b"x" * 1000)
            self.assertTrue(h.is_cached(h.ACCURATE_REPO, e.cache))

    def test_a_truncated_or_dangling_weight_file_is_not_a_cached_model(self):
        h = load_helper()
        with Env() as e:
            e.cache_model(h.ACCURATE_REPO, "weights.safetensors", size=1000)
            self.assertFalse(h.is_cached(h.ACCURATE_REPO, e.cache), "a 1 KB stub is not the model")
            snap = e.cache / ("models--" + h.FAST_REPO.replace("/", "--")) / "snapshots" / ("b" * 40)
            snap.mkdir(parents=True)
            (snap / "model.safetensors").symlink_to(e.cache / "gone")
            self.assertFalse(h.is_cached(h.FAST_REPO, e.cache), "a link to nothing is not the model")

    def test_notice_names_sizes_and_waits_before_anything_is_downloaded(self):
        h = load_helper()
        with Env() as e:
            text = h.notice(h.plan("fast", e.cache))
        for needle in ("Qwen3-ASR", "2.5 GB", "1.6 GB", "4.1 GB", "25 Mbps", "100 Mbps", "500 Mbps", "分鐘", "Hugging Face"):
            self.assertIn(needle, text)

    def test_state_file_matches_what_the_daemon_reads(self):
        with Env() as e:
            r = run([sys.executable, str(HELPER), "state", "--mode", "accurate"], {**e.env, "OPEN_DICTATE_DATA_ROOT": str(e.home / "d")})
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads((e.home / "d" / "dictation-mode.json").read_text())
            self.assertEqual(data["mode"], "accurate")
            r = run([sys.executable, str(HELPER), "state", "--mode", "off"], e.env)
            self.assertNotEqual(r.returncode, 0)


class InstallScript(unittest.TestCase):
    def test_default_plan_is_fast_and_asks_before_downloading(self):
        with Env() as e:
            r = run(["bash", str(INSTALL), "--plan"], e.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("辨識：fast", out)
        self.assertIn("一開始用「快」", out)
        self.assertIn("這次要下載 4.1 GB", out)
        self.assertIn(".venv-asr", out)
        self.assertIn("--plan：不會安裝或下載任何東西", out)

    def test_accurate_and_off_are_options(self):
        with Env() as e:
            acc = run(["bash", str(INSTALL), "--plan", "--recognition", "accurate"], e.env)
            off = run(["bash", str(INSTALL), "--plan", "--recognition=off"], e.env)
        self.assertIn("一開始用「準」", acc.stdout)
        self.assertIn("只用 Whisper", off.stdout)
        self.assertIn("這次要下載 1.6 GB", off.stdout)
        self.assertNotIn(".venv-asr", off.stdout)

    def test_nothing_to_download_says_so(self):
        h = load_helper()
        with Env() as e:
            e.cache_model(h.FAST_REPO, "model.safetensors")
            e.cache_model(h.ACCURATE_REPO, "weights.safetensors")
            r = run(["bash", str(INSTALL), "--plan"], e.env)
        self.assertIn("這次不會下載模型", r.stdout)
        self.assertNotIn("這次要下載", r.stdout)

    def test_bad_option_values_are_refused_before_anything_happens(self):
        with Env() as e:
            for args in (["--recognition", "turbo"], ["--recognition"], ["--bogus"]):
                r = run(["bash", str(INSTALL), *args], e.env)
                self.assertEqual(r.returncode, 64, (args, r.stdout, r.stderr))
            self.assertEqual(list(e.home.iterdir()), [], "a refused run must not touch HOME")

    def test_a_variable_is_never_followed_directly_by_a_full_width_character(self):
        # bash 3.2 in a non-UTF-8 locale (launchd, CI, `LC_ALL=C`) reads the first byte of the next
        # character as part of the name: "$MODE\u3000" dies with "MODE\xe3: unbound variable".
        import re
        rx = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])")
        for rel in ("install.sh", "uninstall.sh", "scripts/doctor.sh"):
            for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                self.assertIsNone(rx.search(line), f"{rel}:{n}: write ${{NAME}} before a non-ASCII character: {line.strip()[:80]}")

    def test_plan_runs_in_a_non_utf8_locale(self):
        with Env() as e:
            r = run(["bash", str(INSTALL), "--plan"], {**e.env, "LC_ALL": "C", "LANG": "C"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_help_lists_the_options(self):
        r = run(["bash", str(INSTALL), "--help"], os.environ.copy())
        self.assertEqual(r.returncode, 0)
        for needle in ("--recognition fast|accurate|off", "--yes", "--plan"):
            self.assertIn(needle, r.stdout)


if __name__ == "__main__":
    unittest.main()
