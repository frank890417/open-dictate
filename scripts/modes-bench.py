#!/usr/bin/env python3
"""modes-bench: speed and character error rate of the recognition models behind the
fast / accurate modes (daemon >= 0.7.0), on public audio only.

Audio is made on the spot: the five fictional sentences of fixtures/calibration-v1.json are
read by the macOS text-to-speech voice (``say``), plus one clip that joins all five, so the
corpus is the same on every Mac and no recording of a person is involved. Nothing is written
into the repository.

What the numbers mean. Speed is real: it is the time the model spends on a clip of that
length, through the same ``asr_engine`` code the daemon's worker and the remote server use.
Accuracy is a sanity check, not a verdict: a synthetic voice is cleaner than a person in a
room, so a model that reads it back correctly may still miss words on a real microphone.

Run it with the recognition venv (daemon/requirements-asr.txt). For the script check
(Simplified output is converted to Traditional by the daemon's glossary layer, so the same
conversion is applied here) also ``pip install opencc-python-reimplemented``:

    .venv-asr/bin/python scripts/modes-bench.py
    .venv-asr/bin/python scripts/modes-bench.py --models qwen3-asr-1.7b,whisper-v3 --reps 7
    .venv-asr/bin/python scripts/modes-bench.py --e2e --daemon-python .venv-dictate/bin/python3

``--e2e`` starts a throw-away daemon (router backend, its own socket and data folder under a
temporary directory, nothing in ~/.open-dictate), switches fast <-> accurate with ``set_mode``
and times whole requests: recognition, glossary, punctuation. It does not touch an installed
daemon or its socket.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))
sys.path.insert(0, str(ROOT / "vendor" / "tools" / "muse-lexicon"))

import asr_engine as E  # noqa: E402

FIXTURE = ROOT / "fixtures" / "calibration-v1.json"
DEFAULT_MODELS = "qwen3-asr-1.7b,whisper-v3,whisper-turbo"
GAP_S = 0.4


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------
def make_clips(workdir: Path, voice: str) -> list[dict]:
    """[{id, truth, wav, audio, dur_s}] — one per fixture sentence, plus all of them joined."""
    if not shutil.which("say") or not shutil.which("afconvert"):
        raise SystemExit("modes-bench needs macOS `say` and `afconvert` to make the test audio")
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    clips = []
    for seg in fx["segments"]:
        aiff, wav = workdir / f"{seg['id']}.aiff", workdir / f"{seg['id']}.wav"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), seg["truth"]], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        clips.append({"id": seg["id"], "truth": seg["truth"], "wav": wav})
    gap = np.zeros(int(GAP_S * E.SAMPLE_RATE), dtype=np.float32)
    parts = []
    for c in clips:
        c["audio"] = E.read_wav(str(c["wav"]))
        parts += [c["audio"], gap]
    joined = np.concatenate(parts[:-1])
    jwav = workdir / "joined.wav"
    pcm = (np.clip(joined, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(jwav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(E.SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    clips.append({"id": "joined", "truth": "".join(c["truth"] for c in clips), "wav": jwav, "audio": joined})
    for c in clips:
        c["dur_s"] = round(len(c["audio"]) / E.SAMPLE_RATE, 2)
    return clips


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def content(text: str) -> str:
    """Letters, digits and CJK only, lower-cased: punctuation and spacing are not recognition."""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "Z", "S", "C")).lower()


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hyp: str, truth: str) -> float:
    t = content(truth)
    return levenshtein(content(hyp), t) / max(1, len(t))


def to_traditional(text: str) -> str | None:
    """The daemon's own Simplified -> Traditional step, or None when opencc is not installed."""
    try:
        import muse_lexicon
    except ImportError:
        return None
    return muse_lexicon.apply_opencc(text) if getattr(muse_lexicon, "HAS_OPENCC", False) else None


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round((len(xs) - 1) * p)))]


# ---------------------------------------------------------------------------
# engine bench
# ---------------------------------------------------------------------------
def bench_engine(models: list[str], clips: list[dict], reps: int) -> list[dict]:
    """All models are loaded first, then every repetition visits every model in turn, so a
    busy machine slows them all alike instead of whichever happened to run during the burst."""
    eng = E.Engine()
    keys = [E.resolve(m) for m in models]
    load_s = {}
    for k in keys:
        print(f"loading {k} ({E.spec(k)['repo']})", flush=True)
        load_s[k] = eng.load(k)
        eng.transcribe(k, clips[0]["audio"])                          # first real call, not timed
    samples = {k: {c["id"]: [] for c in clips} for k in keys}
    texts = {k: {} for k in keys}
    for c in clips:
        for i in range(reps):
            for k in keys:
                r = eng.transcribe(k, c["audio"])
                samples[k][c["id"]].append(r["engine_ms"])
                if i == 0:
                    texts[k][c["id"]] = r["text"]
    out = []
    for k in keys:
        print(f"== {k}", flush=True)
        rows = []
        for c in clips:
            ms, text = samples[k][c["id"]], texts[k][c["id"]]
            trad = to_traditional(text)
            r0 = {"clip": c["id"], "dur_s": c["dur_s"], "ms_median": statistics.median(ms), "ms_min": min(ms),
                  "rtf": round(statistics.median(ms) / 1000 / c["dur_s"], 3),
                  "cer_raw": round(cer(text, c["truth"]), 3),
                  "cer_traditional": None if trad is None else round(cer(trad, c["truth"]), 3), "text": text}
            rows.append(r0)
            print(f"  {c['id']:<10} {c['dur_s']:>5.1f}s  {r0['ms_median']:>6.0f} ms (min {r0['ms_min']})  "
                  f"RTF {r0['rtf']:.3f}  CER raw {r0['cer_raw']:.3f}"
                  + ("" if r0["cer_traditional"] is None else f" / zh-TW {r0['cer_traditional']:.3f}"), flush=True)
        single = [r for r in rows if r["clip"] != "joined"]
        out.append({"model": k, "repo": E.spec(k)["repo"], "load_s": load_s[k], "rows": rows,
                    "median_rtf_sentences": round(statistics.median(r["rtf"] for r in single), 3),
                    "mean_cer_sentences": round(statistics.mean((r["cer_traditional"] if r["cer_traditional"] is not None
                                                                 else r["cer_raw"]) for r in single), 3)})
    return out


# ---------------------------------------------------------------------------
# end-to-end bench through a throw-away daemon
# ---------------------------------------------------------------------------
def rpc(sock_path: str, req: dict, timeout: float = 120.0) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(sock_path)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))


def bench_e2e(args, clips: list[dict], workdir: Path) -> dict:
    sock = str(workdir / "bench.sock")
    data = workdir / "data"
    data.mkdir()
    env = dict(os.environ)
    env.update({
        "OPEN_DICTATE_ASR_BACKEND": "router",
        "OPEN_DICTATE_ASR_WORKER_PYTHON": args.worker_python or sys.executable,
        "OPEN_DICTATE_DATA_ROOT": str(data),
        "OPEN_DICTATE_LOG_ROOT": str(data / "log"),
        "OPEN_DICTATE_PUNCT_WARM": "0",
    })
    for k in ("OPEN_DICTATE_ASR_REMOTE_URL", "OPEN_DICTATE_ASR_SHADOW_MODEL"):
        env.pop(k, None)
    if args.accurate_model:
        env["OPEN_DICTATE_MODEL"] = args.accurate_model
    log = open(workdir / "daemon.log", "w")
    proc = subprocess.Popen([args.daemon_python, str(ROOT / "daemon" / "dictated.py"), "--socket", sock],
                            env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        t0 = time.time()
        while True:
            if proc.poll() is not None:
                raise SystemExit(f"daemon exited early, see {workdir / 'daemon.log'}")
            try:
                ping = rpc(sock, {"cmd": "ping"}, timeout=5)
                if ping.get("warm"):
                    break
            except (OSError, ValueError):
                pass
            if time.time() - t0 > args.warm_timeout:
                raise SystemExit(f"daemon not warm after {args.warm_timeout:.0f}s, see {workdir / 'daemon.log'}")
            time.sleep(1)
        print(f"daemon {ping.get('version')} warm in {time.time() - t0:.0f}s; mode={ping.get('mode')}", flush=True)
        modes = ("fast", "accurate")
        acc = {m: {"model": None, "rows": []} for m in modes}
        for c in clips:
            seen = {m: {"total": [], "asr": [], "text": "", "punct": ""} for m in modes}
            for i in range(args.reps + 1):                            # round 0 warms each mode's path
                for mode in modes:                                    # alternate, so load drift hits both alike
                    sw = rpc(sock, {"cmd": "set_mode", "mode": mode})
                    assert sw.get("ok") and sw.get("mode") == mode, sw
                    acc[mode]["model"] = sw.get("model")
                    wav = workdir / f"req-{mode}-{c['id']}-{i}.wav"
                    shutil.copyfile(c["wav"], wav)                    # the daemon deletes wavs it has processed
                    r = rpc(sock, {"cmd": "transcribe", "wav": str(wav), "punct": "smart_zh"})
                    if not r.get("ok"):
                        raise SystemExit(f"{mode}/{c['id']}: {r}")
                    if i:
                        seen[mode]["total"].append(r["total_ms"])
                        seen[mode]["asr"].append(r["asr_ms"])
                    if i == 1:
                        seen[mode]["text"], seen[mode]["punct"] = r["text"], r["punct"]
            for mode in modes:
                v = seen[mode]
                row = {"clip": c["id"], "dur_s": c["dur_s"], "total_ms_median": statistics.median(v["total"]),
                       "total_ms_min": min(v["total"]), "asr_ms_median": statistics.median(v["asr"]),
                       "cer": round(cer(v["text"], c["truth"]), 3), "punct": v["punct"], "text": v["text"]}
                acc[mode]["rows"].append(row)
                print(f"  {mode:<8} {c['id']:<10} {c['dur_s']:>5.1f}s  total {row['total_ms_median']:>6.0f} ms "
                      f"(min {row['total_ms_min']})  asr {row['asr_ms_median']:.0f}  punct {row['punct']}  CER {row['cer']:.3f}",
                      flush=True)
        results = acc
        return results
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


# ---------------------------------------------------------------------------
def machine() -> dict:
    chip = ""
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    except OSError:
        pass
    return {"chip": chip, "macos": platform.mac_ver()[0], "python": platform.python_version(),
            "load_avg_1m": round(os.getloadavg()[0], 1), "cores": os.cpu_count()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default=DEFAULT_MODELS, help="comma list of asr_engine keys or repo ids")
    ap.add_argument("--reps", type=int, default=5, help="timed runs per clip (median is reported)")
    ap.add_argument("--voice", default="Meijia", help="macOS `say` voice for the test audio (a Taiwan Mandarin voice)")
    ap.add_argument("--json", help="also write the full result to this file")
    ap.add_argument("--e2e", action="store_true", help="time whole requests through a throw-away daemon, fast and accurate")
    ap.add_argument("--daemon-python", default=sys.executable, help="python for daemon/dictated.py (needs requirements.txt)")
    ap.add_argument("--worker-python", help="python for the recognition worker (default: this one)")
    ap.add_argument("--accurate-model", help="model of the accurate mode (default: the daemon's MODEL)")
    ap.add_argument("--warm-timeout", type=float, default=300.0)
    args = ap.parse_args()

    result: dict = {"machine": machine()}
    with tempfile.TemporaryDirectory(prefix="od-modes-bench-") as tmp:
        work = Path(tmp)
        clips = make_clips(work, args.voice)
        result["clips"] = [{"id": c["id"], "dur_s": c["dur_s"]} for c in clips]
        print(f"{result['machine']}\nclips: " + ", ".join(f"{c['id']} {c['dur_s']}s" for c in clips), flush=True)
        if args.e2e:
            result["e2e"] = bench_e2e(args, clips, work)
        else:
            result["engine"] = bench_engine([m for m in args.models.split(",") if m.strip()], clips, args.reps)
            print("\nmodel               load   median RTF (sentences)   mean CER (sentences)")
            for m in result["engine"]:
                print(f"{m['model']:<18} {m['load_s']:>5.1f}s  {m['median_rtf_sentences']:>10.3f}"
                      f"             {m['mean_cer_sentences']:>10.3f}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
