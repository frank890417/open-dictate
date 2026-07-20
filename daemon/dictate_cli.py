#!/usr/bin/env python3
"""dictate_cli — dictated.py 測試/手動 CLI（純 stdlib，系統 python3 可跑）

用法：
    python3 dictate_cli.py ping
    python3 dictate_cli.py file <wav>        # 直接送該路徑（/tmp 下的 wav 會被 daemon 刪除）
    python3 dictate_cli.py bench <wav> --n 3 # 每輪複製到 /tmp 再送（模擬殼行為），報中位數
    python3 dictate_cli.py reload            # reload_lexicon（三庫重載）
    python3 dictate_cli.py stats             # 今日聽寫摘要（daemon ≥0.5）
    python3 dictate_cli.py add <wrong> <right>  # 教詞庫（daemon ≥0.5）

協議見 ~/Projects/open-dictate/IO-CONTRACT.md。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import sys
import time

SOCKET_PATH = "/tmp/open-dictate.sock"


def send(req: dict, socket_path: str, timeout: float = 120.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(socket_path)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        raise RuntimeError("empty response from daemon")
    return json.loads(line.decode("utf-8"))


def pretty(resp: dict) -> str:
    return json.dumps(resp, ensure_ascii=False, indent=2)


def cmd_ping(args) -> int:
    t0 = time.perf_counter()
    resp = send({"cmd": "ping"}, args.socket, timeout=10)
    wall_ms = (time.perf_counter() - t0) * 1000
    print(pretty(resp))
    print(f"(round-trip {wall_ms:.1f}ms)", file=sys.stderr)
    return 0 if resp.get("ok") else 1


def cmd_file(args) -> int:
    wav = os.path.abspath(os.path.expanduser(args.wav))
    if not os.path.isfile(wav):
        print(f"❌ file not found: {wav}", file=sys.stderr)
        return 1
    if os.path.realpath(wav).startswith(("/tmp/", "/private/tmp/")):
        print("ℹ️ 注意：/tmp 下的 wav 處理完會被 daemon 刪除（契約行為）", file=sys.stderr)
    t0 = time.perf_counter()
    resp = send({"cmd": "transcribe", "wav": wav}, args.socket)
    wall_ms = (time.perf_counter() - t0) * 1000
    print(pretty(resp))
    print(f"(round-trip {wall_ms:.1f}ms)", file=sys.stderr)
    return 0 if resp.get("ok") else 1


def cmd_bench(args) -> int:
    src = os.path.abspath(os.path.expanduser(args.wav))
    if not os.path.isfile(src):
        print(f"❌ file not found: {src}", file=sys.stderr)
        return 1

    asr, total, wall = [], [], []
    last = None
    deleted_ok = True
    for i in range(args.n):
        # 模擬殼行為：每輪複製一份到 /tmp（daemon 處理完會刪掉這份 copy，原檔安全）
        copy = f"/tmp/open-dictate-bench-{os.getpid()}-{i}.wav"
        shutil.copyfile(src, copy)
        t0 = time.perf_counter()
        resp = send({"cmd": "transcribe", "wav": copy}, args.socket)
        wall_ms = (time.perf_counter() - t0) * 1000
        if os.path.exists(copy):
            deleted_ok = False
            os.unlink(copy)  # daemon 沒刪就自己清，不留殘檔
        if not resp.get("ok"):
            print(f"❌ run {i + 1}/{args.n} failed: {pretty(resp)}", file=sys.stderr)
            return 1
        asr.append(resp["asr_ms"])
        total.append(resp["total_ms"])
        wall.append(wall_ms)
        last = resp
        print(
            f"  run {i + 1}/{args.n}: asr {resp['asr_ms']}ms | total {resp['total_ms']}ms "
            f"| wall {wall_ms:.0f}ms",
            file=sys.stderr,
        )

    name = os.path.basename(src)
    print(f"\n== bench {name} (n={args.n}) ==")
    print(f"asr_ms   median {statistics.median(asr):.0f}  (min {min(asr)} / max {max(asr)})")
    print(f"total_ms median {statistics.median(total):.0f}  (min {min(total)} / max {max(total)})")
    print(f"wall_ms  median {statistics.median(wall):.0f}")
    print(f"daemon deleted tmp wav: {'yes' if deleted_ok else 'NO (contract violation)'}")
    print(f"text: {last['text']}")
    if last.get("raw") != last.get("text"):
        print(f"raw : {last['raw']}")
    if last.get("changes"):
        print(f"changes: {json.dumps(last['changes'], ensure_ascii=False)}")
    return 0


def cmd_reload(args) -> int:
    resp = send({"cmd": "reload_lexicon"}, args.socket, timeout=30)
    print(pretty(resp))
    return 0 if resp.get("ok") else 1


def cmd_stats(args) -> int:
    resp = send({"cmd": "stats"}, args.socket, timeout=10)
    print(pretty(resp))
    return 0 if resp.get("ok") else 1


def cmd_add(args) -> int:
    resp = send(
        {"cmd": "add_pair", "wrong": args.wrong, "right": args.right, "source": args.source},
        args.socket,
        timeout=30,
    )
    print(pretty(resp))
    return 0 if resp.get("ok") else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="dictate_cli.py", description=__doc__.split("\n")[1])
    parser.add_argument("--socket", default=SOCKET_PATH, help=f"socket 路徑（預設 {SOCKET_PATH}）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ping", help="daemon 健康檢查")

    p_file = sub.add_parser("file", help="轉錄單一 wav（路徑直送）")
    p_file.add_argument("wav")

    p_bench = sub.add_parser("bench", help="重複轉錄測延遲（每輪複製到 /tmp，取中位數）")
    p_bench.add_argument("wav")
    p_bench.add_argument("--n", type=int, default=3, help="重複次數（預設 3）")

    sub.add_parser("reload", help="reload_lexicon（重載三庫）")
    sub.add_parser("stats", help="今日聽寫統計（daemon ≥0.5）")

    p_add = sub.add_parser("add", help="教詞庫 pair（daemon ≥0.5）")
    p_add.add_argument("wrong")
    p_add.add_argument("right")
    p_add.add_argument("--source", default="dictate-cli")

    args = parser.parse_args()
    try:
        return {
            "ping": cmd_ping,
            "file": cmd_file,
            "bench": cmd_bench,
            "reload": cmd_reload,
            "stats": cmd_stats,
            "add": cmd_add,
        }[args.command](args)
    except (ConnectionRefusedError, FileNotFoundError):
        print(f"❌ daemon 沒起來（{args.socket} 連不上）", file=sys.stderr)
        return 2
    except socket.timeout:
        print("❌ daemon 回應逾時", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
