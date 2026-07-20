#!/usr/bin/env python3
"""測試用假 daemon — 只給 Swift 殼做 socket 協議 headless 驗證。

⚠️ 這不是正式 daemon。正式 daemon 在 daemon/dictated.py（另一條線負責）。
協議 SSOT：IO-CONTRACT.md。

用法：
    python3 scripts/fake-daemon.py [socket路徑] [模式]

模式：
    normal    （預設）transcribe 回固定校正文字
    no_speech transcribe 一律回 {"ok":false,"error":"no_speech"}
    slow      transcribe 睡 12s 才回（測殼的 10s timeout）

Ctrl-C 結束；會自己清 socket 檔。
"""
import json
import os
import socket
import sys
import time

SOCK_PATH = sys.argv[1] if len(sys.argv) > 1 else "/tmp/open-dictate.sock"
MODE = sys.argv[2] if len(sys.argv) > 2 else "normal"


def handle(req: dict) -> dict:
    cmd = req.get("cmd")
    if cmd == "ping":
        return {"ok": True, "pong": True,
                "model": "mlx-community/whisper-large-v3-turbo", "warm": True}
    if cmd == "reload_lexicon":
        return {"ok": True, "reloaded": True, "pairs": 123}
    if cmd == "transcribe":
        wav = req.get("wav", "")
        if MODE == "slow":
            time.sleep(12)
        if MODE == "no_speech":
            return {"ok": False, "error": "no_speech"}
        if not os.path.exists(wav):
            return {"ok": False, "error": "file_not_found"}
        # 契約：daemon 處理完刪除 wav
        try:
            os.unlink(wav)
        except OSError:
            pass
        return {"ok": True,
                "text": "假 daemon 校正後文字（OpenDictate 測試）",
                "raw": "假daemon校正后文字（台灣點人力測試）",
                "changes": [["台灣點人力", "OpenDictate"]],
                "asr_ms": 210, "total_ms": 260}
    return {"ok": False, "error": "asr_failed"}


def main():
    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCK_PATH)
    server.listen(4)
    print(f"[fake-daemon] listening {SOCK_PATH} mode={MODE}", flush=True)
    try:
        while True:
            conn, _ = server.accept()
            with conn:
                buf = b""
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if not buf:
                    continue
                line = buf.split(b"\n", 1)[0]
                try:
                    req = json.loads(line)
                except json.JSONDecodeError:
                    resp = {"ok": False, "error": "asr_failed"}
                else:
                    print(f"[fake-daemon] <- {req}", flush=True)
                    resp = handle(req)
                conn.sendall(json.dumps(resp, ensure_ascii=False).encode() + b"\n")
                print(f"[fake-daemon] -> {resp}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        if os.path.exists(SOCK_PATH):
            os.unlink(SOCK_PATH)


if __name__ == "__main__":
    main()
