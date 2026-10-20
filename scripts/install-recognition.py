#!/usr/bin/env python3
"""Install-time helper for ./install.sh: which recognition models a setup needs, what is
already on this Mac, what the download costs, and the download itself.

Standard library only for `plan` / `needs-download` / `state`, because ./install.sh runs them
with the system python3 BEFORE any virtual environment exists: the user is told the size and
the wait, and asked, before anything is downloaded. `download` is the one command that needs
huggingface_hub; install.sh runs it with the environment's python after the user said yes.

    install-recognition.py plan --mode fast|accurate|off
    install-recognition.py needs-download --mode ...     # exit 0 = something to download, 1 = all cached
    install-recognition.py download --mode ...
    install-recognition.py state --mode fast|accurate    # write the remembered mode (<data root>/dictation-mode.json)

Modes: fast and accurate both set up the router backend, which keeps the fast model (Qwen3-ASR
1.7B) and the accurate model (Whisper large-v3-turbo, the daemon's default MODEL) loaded so the
menu bar can switch between them; they differ only in the mode the daemon starts in. off keeps the
0.6.x setup: one Whisper model inside the daemon.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

# Same repos as daemon/asr_engine.py MODELS["qwen3-asr-1.7b"] and daemon/dictated.py MODEL; the
# test suite compares them, because this file must run before numpy exists.
FAST_REPO = "mlx-community/Qwen3-ASR-1.7B-8bit"
ACCURATE_REPO = "mlx-community/whisper-large-v3-turbo"
MODES = ("fast", "accurate", "off")

# Sizes read from the Hugging Face API on 2026-10-20 (sum of the files in each repo) and the
# weight file whose presence means the download finished.
KNOWN = {
    FAST_REPO: {"bytes": 2_470_000_000, "weights": "model.safetensors", "label": "Qwen3-ASR 1.7B (fast)"},
    ACCURATE_REPO: {"bytes": 1_610_000_000, "weights": "weights.safetensors", "label": "Whisper large-v3-turbo (accurate)"},
}
OFF_LABEL = "Whisper large-v3-turbo"
MIN_RAM_GIB = 16
MIN_FRACTION = 0.9   # the weight file must be at least this much of the size we expect from the repo
RATES_MBPS = (25, 100, 500)


def repos_for(mode: str) -> list[str]:
    if mode == "off":
        return [ACCURATE_REPO]
    return [FAST_REPO, ACCURATE_REPO]


def hub_cache() -> Path:
    """Where huggingface_hub keeps models: HF_HUB_CACHE, else $HF_HOME/hub, else the XDG/home default."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base).expanduser() / "huggingface" / "hub"


def is_cached(repo: str, cache: Path | None = None) -> bool:
    """A finished snapshot of the repo is on disk.

    huggingface_hub links a file into snapshots/<revision>/ only after that file has been fully
    downloaded, so the weight file being there (the link resolves, and the file is nearly the
    expected size) is the signal. A leftover `*.incomplete` blob from an earlier interrupted run
    does NOT mean the model is missing: huggingface_hub does not clean it up when the next run
    succeeds (found by killing a download and running it again).
    """
    root = (cache or hub_cache()) / ("models--" + repo.replace("/", "--"))
    snaps = root / "snapshots"
    if not snaps.is_dir():
        return False
    known = KNOWN.get(repo)
    for snap in snaps.iterdir():
        if known:
            try:
                size = (snap / known["weights"]).stat().st_size      # follows the link; a broken link raises
            except OSError:
                continue
            if size >= known["bytes"] * MIN_FRACTION:
                return True
        elif snap.is_dir() and any(snap.iterdir()):
            return True
    return False


def gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def minutes(nbytes: float, mbps: int) -> str:
    sec = nbytes * 8 / (mbps * 1e6)
    return "不到 1 分鐘" if sec < 60 else f"約 {round(sec / 60)} 分鐘"


def ram_gib() -> float | None:
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 2**30
    except (ValueError, OSError, AttributeError):
        return None


def plan(mode: str, cache: Path | None = None) -> dict:
    items = []
    for repo in repos_for(mode):
        info = KNOWN[repo]
        items.append({"repo": repo, "label": OFF_LABEL if mode == "off" else info["label"], "bytes": info["bytes"],
                      "cached": is_cached(repo, cache)})
    missing = sum(i["bytes"] for i in items if not i["cached"])
    return {"mode": mode, "models": items, "missing_bytes": missing, "needs_download": missing > 0,
            "ram_gib": ram_gib(), "ram_low": mode != "off" and (ram_gib() or MIN_RAM_GIB) < MIN_RAM_GIB}


def notice(p: dict) -> str:
    lines = []
    if p["mode"] == "off":
        lines.append("辨識設定：只用 Whisper（不開快／準模式）。")
    else:
        first = "快" if p["mode"] == "fast" else "準"
        lines.append(f"辨識設定：快／準兩種模式都裝好，一開始用「{first}」。之後在選單列或 `python3 daemon/dictate_cli.py mode` 切換。")
    lines.append("模型不在這個 repo 裡，會由你的 Mac 從 Hugging Face 下載：")
    for i in p["models"]:
        lines.append(f"  • {i['label']}（{i['repo']}）{gb(i['bytes'])}　{'已在本機，不用再下載' if i['cached'] else '要下載'}")
    if p["needs_download"]:
        b = p["missing_bytes"]
        eta = "、".join(f"{r} Mbps {minutes(b, r)}" for r in RATES_MBPS)
        lines.append(f"這次要下載 {gb(b)}。下載時間看你的網路：{eta}。")
        lines.append("模型下載之外，Python 套件（兩個虛擬環境）另外佔約 2.6 GB 磁碟。下載可以中斷，重跑 ./install.sh 會繼續，已經下載完成的檔案不會重抓。")
    else:
        lines.append("需要的模型都已在本機，這次不會下載模型。")
    if p["ram_low"]:
        lines.append(f"⚠️ 這台 Mac 的記憶體約 {p['ram_gib']:.0f} GB。兩個模型一起常駐約需 5 GB 可用記憶體，記憶體吃緊的話建議改用 ./install.sh --recognition off。")
    return "\n".join(lines)


def write_state(mode: str) -> Path:
    """Remember the mode like daemon/dictated.py does ({"mode", "ts"} in <data root>/dictation-mode.json)."""
    data_root = Path(os.environ.get("OPEN_DICTATE_DATA_ROOT", "~/.open-dictate")).expanduser()
    path = Path(os.environ.get("OPEN_DICTATE_ASR_MODE_STATE", str(data_root / "dictation-mode.json"))).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": mode, "ts": datetime.datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    return path


def download(mode: str) -> int:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少 huggingface_hub：請用 install.sh 建好的虛擬環境執行這個指令", file=sys.stderr)
        return 2
    for repo in repos_for(mode):
        if is_cached(repo):
            print(f"✓ 已在本機：{repo}", flush=True)
            continue
        print(f"▸ 下載 {repo}（{gb(KNOWN[repo]['bytes'])}）", flush=True)
        t0 = time.time()
        try:
            snapshot_download(repo)
        except Exception as e:  # noqa: BLE001 — network, disk, auth: say what and let the user re-run
            print(f"✗ 下載失敗：{repo}：{e.__class__.__name__}: {e}", file=sys.stderr)
            print("  重跑 ./install.sh 會繼續，已經下載完成的檔案不會重抓。", file=sys.stderr)
            return 1
        if not is_cached(repo):
            print(f"✗ 下載完成但找不到權重檔：{repo}", file=sys.stderr)
            return 1
        print(f"✓ 完成 {repo}（{time.time() - t0:.0f} 秒）", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("plan", "needs-download", "download", "state"))
    ap.add_argument("--mode", choices=MODES, required=True)
    ap.add_argument("--json", action="store_true", help="plan: machine-readable")
    args = ap.parse_args()
    if args.command == "plan":
        p = plan(args.mode)
        print(json.dumps(p, ensure_ascii=False, indent=2) if args.json else notice(p))
        return 0
    if args.command == "needs-download":
        return 0 if plan(args.mode)["needs_download"] else 1
    if args.command == "download":
        return download(args.mode)
    if args.mode == "off":
        print("state: off has no mode to remember", file=sys.stderr)
        return 2
    print(write_state(args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
