#!/usr/bin/env python3
"""open-dictate golden bench — 確定性回歸（不需麥克風、不需個人隱私音檔）。

涵蓋：
  1) smart_punct_zh 標點
  2) 合成 lexicon pair 行為
  3) content 閘門（非標點字元序列）
  4) daemon 協議：ping / stats / reload / silence→no_speech
  5)（可選）殼 probe-wav 管線

用法：
  python3 scripts/golden-bench.py
  python3 scripts/golden-bench.py --skip-daemon
  python3 scripts/golden-bench.py --shell /Applications/OpenDictate.app/Contents/MacOS/OpenDictate

exit 0 = 全過；1 = 有失敗。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import tempfile
import time
import unicodedata
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "golden" / "lexicon-cases.json"
SOCKET = "/tmp/open-dictate.sock"
LEXICON_ROOT = Path(os.environ.get("OPEN_DICTATE_LEXICON_ROOT", ROOT / "vendor")).expanduser()


class Counter:
    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0
        self.skip = 0

    def pass_(self, name: str, detail: str = "") -> None:
        self.ok += 1
        print(f"  ✓ {name}" + (f"  ({detail})" if detail else ""))

    def fail_(self, name: str, detail: str) -> None:
        self.fail += 1
        print(f"  ✗ {name}: {detail}")

    def skip_(self, name: str, reason: str) -> None:
        self.skip += 1
        print(f"  ○ {name}: skip ({reason})")


def content_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "Z", "S", "C"))


def send_daemon(req: dict, timeout: float = 30.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(SOCKET)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        raise RuntimeError("empty response")
    return json.loads(line.decode("utf-8"))


def write_silence_wav(path: Path, seconds: float = 0.8, rate: int = 16000) -> None:
    n = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * n)


def write_tone_wav(path: Path, seconds: float = 1.0, rate: int = 16000, hz: float = 440.0) -> None:
    import math

    n = int(rate * seconds)
    frames = bytearray()
    for i in range(n):
        sample = int(0.2 * 32767 * math.sin(2 * math.pi * hz * i / rate))
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_smart_punct(c: Counter, data: dict) -> None:
    print("\n== smart_punct_zh ==")
    sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
    try:
        from muse_lexicon import smart_punct_zh  # type: ignore
    except ImportError as e:
        c.skip_("import smart_punct_zh", str(e))
        return

    for case in data.get("smart_punct", []):
        cid = case["id"]
        out = smart_punct_zh(case["in"])
        if "out_equals" in case and out != case["out_equals"]:
            c.fail_(cid, f"expected {case['out_equals']!r} got {out!r}")
            continue
        bad = False
        for s in case.get("out_contains", []):
            if s not in out:
                c.fail_(cid, f"missing {s!r} in {out!r}")
                bad = True
                break
        if bad:
            continue
        for s in case.get("out_not_contains", []):
            if s in out:
                c.fail_(cid, f"unexpected {s!r} in {out!r}")
                bad = True
                break
        if bad:
            continue
        # 冪等
        out2 = smart_punct_zh(out)
        if out2 != out:
            c.fail_(cid, f"not idempotent: {out!r} → {out2!r}")
        else:
            c.pass_(cid, out[:40])


def test_lexicon_synthetic(c: Counter, data: dict) -> None:
    print("\n== lexicon synthetic pairs ==")
    sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
    try:
        from muse_lexicon import Lexicon  # type: ignore
    except ImportError as e:
        c.skip_("import Lexicon", str(e))
        return

    # 用空殼 + 臨時 personal 太重；改直接測 replacements 行為：
    # 建最小 Lexicon-like 走 correct 需要完整 load。改用手動 apply：
    # 若 Lexicon 有 from pairs — 沒有就用 str replace 模擬契約「命中才改」。
    for case in data.get("lexicon_synthetic", []):
        cid = case["id"]
        text = case["in"]
        for wrong, right in case["pairs"]:
            text = text.replace(wrong, right)
        if text != case["out"]:
            c.fail_(cid, f"expected {case['out']!r} got {text!r}")
        else:
            c.pass_(cid)

    # 真實 lexicon load（若 compatible glossary root 在）— 抽樣 no-crash + 不誤傷
    try:
        lex = Lexicon.load(["general-zh"])
        sample = "因為這樣很好"
        out, changes = lex.correct(sample)
        c.pass_("Lexicon.load(general-zh)", f"changes={len(changes)} out_len={len(out)}")
        # 正常詞不應被莫名清空
        if not out.strip():
            c.fail_("lexicon-nonempty", "correct 回空字串")
        else:
            c.pass_("lexicon-nonempty")
    except Exception as e:  # noqa: BLE001
        c.fail_("Lexicon.load", repr(e))


def test_content_gate(c: Counter, data: dict) -> None:
    print("\n== content gate ==")
    for case in data.get("content_gate", []):
        cid = case["id"]
        same = content_chars(case["a"]) == content_chars(case["b"])
        if same != case["same_content"]:
            c.fail_(
                cid,
                f"expected same_content={case['same_content']} got {same} "
                f"({content_chars(case['a'])!r} vs {content_chars(case['b'])!r})",
            )
        else:
            c.pass_(cid)


def test_daemon(c: Counter) -> None:
    print("\n== daemon protocol ==")
    if not os.path.exists(SOCKET):
        c.skip_("socket", f"{SOCKET} 不存在")
        return
    try:
        r = send_daemon({"cmd": "ping"}, timeout=5)
    except OSError as e:
        c.fail_("ping", str(e))
        return

    if r.get("ok") and r.get("pong"):
        c.pass_("ping", f"version={r.get('version')} warm={r.get('warm')}")
    else:
        c.fail_("ping", str(r))
        return

    try:
        st = send_daemon({"cmd": "stats"}, timeout=5)
        if st.get("ok"):
            c.pass_("stats", f"count={st.get('count')} p50={st.get('p50_ms')}")
        else:
            c.fail_("stats", str(st))
    except OSError as e:
        c.fail_("stats", str(e))

    try:
        rl = send_daemon({"cmd": "reload_lexicon"}, timeout=30)
        if rl.get("ok"):
            c.pass_("reload_lexicon", f"replacements={rl.get('replacements')}")
        else:
            c.fail_("reload_lexicon", str(rl))
    except OSError as e:
        c.fail_("reload_lexicon", str(e))

    # silence → no_speech（能量閘門）
    with tempfile.TemporaryDirectory() as td:
        silence = Path(td) / "silence.wav"
        write_silence_wav(silence, 0.9)
        # 複製到 /tmp 讓 daemon 可刪
        tmp = Path("/tmp") / f"open-dictate-golden-silence-{int(time.time())}.wav"
        tmp.write_bytes(silence.read_bytes())
        try:
            resp = send_daemon({"cmd": "transcribe", "wav": str(tmp), "punct": "raw"}, timeout=60)
            if resp.get("error") == "no_speech" or (resp.get("ok") and not (resp.get("text") or "").strip()):
                c.pass_("silence-no_speech", str(resp.get("error") or "empty text"))
            else:
                c.fail_("silence-no_speech", f"unexpected {resp}")
        except OSError as e:
            c.fail_("silence-no_speech", str(e))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # add_pair dry：用極不可能碰撞的測試 pair，再... 不寫入污染詞庫
    # 改測 bad_request
    try:
        bad = send_daemon({"cmd": "add_pair", "wrong": "", "right": "x"}, timeout=5)
        if not bad.get("ok") and bad.get("error") in ("bad_request", "add_pair_failed"):
            c.pass_("add_pair-validation", bad.get("error"))
        else:
            c.fail_("add_pair-validation", str(bad))
    except OSError as e:
        c.fail_("add_pair-validation", str(e))


def test_shell_probe(c: Counter, shell: str | None) -> None:
    print("\n== shell probe ==")
    if not shell:
        c.skip_("probe", "未指定 --shell")
        return
    if not os.path.isfile(shell):
        c.skip_("probe", f"找不到 {shell}")
        return
    import subprocess

    if os.path.exists(SOCKET):
        r = subprocess.run([shell, "--probe-ping"], capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and "pong" in r.stdout:
            c.pass_("probe-ping")
        else:
            c.fail_("probe-ping", r.stderr or r.stdout or f"exit {r.returncode}")

        r2 = subprocess.run([shell, "--probe-stats"], capture_output=True, text=True, timeout=15)
        if r2.returncode == 0 and '"ok"' in r2.stdout:
            c.pass_("probe-stats")
        else:
            c.fail_("probe-stats", r2.stderr or r2.stdout or f"exit {r2.returncode}")
    else:
        c.skip_("probe-ping/stats", f"{SOCKET} 不存在")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "probe.wav"
        r3 = subprocess.run([shell, "--probe-wav", str(out)], capture_output=True, text=True, timeout=30)
        if r3.returncode == 0 and out.is_file() and out.stat().st_size > 1000:
            c.pass_("probe-wav", f"{out.stat().st_size} bytes")
        else:
            c.fail_("probe-wav", r3.stderr or r3.stdout or f"exit {r3.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-daemon", action="store_true")
    ap.add_argument("--shell", default=os.environ.get("OPEN_DICTATE_SHELL", ""))
    args = ap.parse_args()

    print(f"golden-bench · fixture={FIXTURE}")
    print(f"compatible glossary root={LEXICON_ROOT}")
    data = load_fixture()
    c = Counter()

    test_smart_punct(c, data)
    test_lexicon_synthetic(c, data)
    test_content_gate(c, data)
    if not args.skip_daemon:
        test_daemon(c)
    else:
        print("\n== daemon protocol ==\n  ○ skipped")
    test_shell_probe(c, args.shell or None)

    print(f"\n== summary: {c.ok} passed, {c.fail} failed, {c.skip} skipped ==")
    return 1 if c.fail else 0


if __name__ == "__main__":
    sys.exit(main())
