#!/usr/bin/env python3
"""asr-bench — open-dictate ASR 層 A/B（Stage 2，2026-07-16）。

比較 ASR 模型（whisper-large-v3-turbo vs Breeze-ASR-25 等）在real user voice samples上的表現。
規劃 SSOT：docs/AB-EVAL-PLAN.md §Stage 2。

語料：會議音檔（16k mono PCM16 wav）＋ muse-meeting 逐字稿（`[MM:SS] 講者：text`）。
以逐字稿行距切出目標講者的獨白段，兩模型同 prompt 轉錄，過同一層詞庫校正後比較。

⚠️ ground truth 誠實標註：參考稿本身是「turbo ASR → 詞庫校正 → 清洗」的產物，
CER 對 turbo 有結構性偏向（turbo 的錯若沒被校正修掉，會被當成「正確答案」）。
所以 CER 只當粗訊號；客觀軸是專名命中（raw 層＝ASR 本領）與延遲 RTF；
分歧處列 diff 供人眼判。乾淨 ground truth 要等校準稿朗讀（L3）。

跑法（td-subtitle venv）：
  python3 scripts/asr-bench.py \
      --wav /tmp/260716_a collaborator晨會.16k.wav \
      --transcript ~/Downloads/"260716 a collaborator晨會.會議逐字稿.txt" \
      --speaker user
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON_ROOT = Path(os.environ.get("OPEN_DICTATE_LEXICON_ROOT", ROOT / "vendor")).expanduser()
EVAL_DIR = Path("~/.open-dictate/dictation-eval").expanduser()

sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
sys.path.insert(0, str(ROOT / "daemon"))

import numpy as np  # noqa: E402
import dictated  # noqa: E402（生產常數：prompt 種子/優先詞表/content_chars）
from muse_lexicon import Lexicon  # noqa: E402

SR = 16000
DEFAULT_MODELS = "mlx-community/whisper-large-v3-turbo,david20571015/Breeze-ASR-25-mlx-fp16"
LINE_RE = re.compile(r"^\[(\d+):(\d\d)\]\s*(\S+?)[：:]\s*(.*)$")


def content_chars(t: str) -> str:
    return dictated._content_chars(t)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def parse_transcript(path: Path) -> list[dict]:
    """`[MM:SS] 講者：text` → [{start, end, speaker, text}]；end = 下一行 start。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        start = int(m.group(1)) * 60 + int(m.group(2))
        text = m.group(4).replace(" - ", "").replace("- ", "").strip()
        rows.append({"start": start, "speaker": m.group(3), "text": text})
    for i, r in enumerate(rows):
        r["end"] = rows[i + 1]["start"] if i + 1 < len(rows) else r["start"] + 30
    return rows


def pick_segments(rows: list[dict], speaker: str, min_s: float, max_s: float,
                  max_segments: int) -> list[dict]:
    segs = [r for r in rows
            if r["speaker"] == speaker
            and min_s <= (r["end"] - r["start"]) <= max_s
            and len(content_chars(r["text"])) >= 20]
    # 取最長的 N 段（資訊量大），輸出按時間序
    segs = sorted(segs, key=lambda r: r["end"] - r["start"], reverse=True)[:max_segments]
    return sorted(segs, key=lambda r: r["start"])


def slice_wav(src: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(src), "rb") as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2 and w.getnchannels() == 1, \
            "語料 wav 必須是 16k mono PCM16（muse-meeting 的 .16k.wav 即是）"
        w.setpos(int(start * SR))
        data = w.readframes(int((end - start) * SR))
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def build_prompt(lex: Lexicon) -> str:
    """與 daemon._build_prompt 同構（生產 prompt，兩模型同一份＝公平）。"""
    return dictated.PUNCT_STYLE_SEED + lex.build_initial_prompt(max_chars=150) + "、" + dictated.DICTATE_PRIORITY_TERMS


def transcribe(model: str, audio: np.ndarray, prompt: str) -> tuple[str, int]:
    import mlx_whisper
    t0 = time.perf_counter()
    result = mlx_whisper.transcribe(audio, path_or_hf_repo=model,
                                    language="zh", initial_prompt=prompt)
    ms = int((time.perf_counter() - t0) * 1000)
    return (result.get("text") or "").strip(), ms


def term_hits(text: str, terms: list[str]) -> set[str]:
    c = content_chars(text)
    return {t for t in terms if t in c}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wav", required=True, help="16k mono PCM16 wav（muse-meeting .16k.wav）")
    ap.add_argument("--transcript", required=True, help="muse-meeting 逐字稿（[MM:SS] 講者：text）")
    ap.add_argument("--speaker", default="user")
    ap.add_argument("--models", default=DEFAULT_MODELS)
    ap.add_argument("--min-s", type=float, default=8.0)
    ap.add_argument("--max-s", type=float, default=45.0)
    ap.add_argument("--max-segments", type=int, default=10)
    args = ap.parse_args()

    wav = Path(args.wav).expanduser()
    transcript = Path(args.transcript).expanduser()
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    lex = Lexicon.load()
    prompt = build_prompt(lex)
    terms = [content_chars(t) for t in
             (list(lex.canonical) + [x.strip() for x in dictated.DICTATE_PRIORITY_TERMS.split("、")])]
    terms = sorted({t for t in terms if len(t) >= 2})

    rows = parse_transcript(transcript)
    segs = pick_segments(rows, args.speaker, args.min_s, args.max_s, args.max_segments)
    if not segs:
        print("找不到符合條件的段落。")
        return 1
    total_audio = sum(s["end"] - s["start"] for s in segs)
    print(f"語料：{len(segs)} 段 {args.speaker} 獨白，共 {total_audio:.0f}s（{wav.name}）")

    # 每模型先 warmup（1s 靜音），load+compile 不進延遲統計
    results: dict[str, list[dict]] = {m: [] for m in models}
    warm: dict[str, float] = {}
    for model in models:
        print(f"\n== {model} ==")
        t0 = time.perf_counter()
        transcribe(model, np.zeros(SR, dtype=np.float32), prompt)
        warm[model] = time.perf_counter() - t0
        print(f"  warmup(load+compile): {warm[model]:.1f}s")
        for s in segs:
            audio = slice_wav(wav, s["start"], s["end"])
            raw, ms = transcribe(model, audio, prompt)
            corrected, changes = lex.correct(raw)
            dur = s["end"] - s["start"]
            results[model].append({
                "start": s["start"], "dur": dur, "ref": s["text"],
                "raw": raw, "corrected": corrected, "ms": ms,
                "rtf": round(dur / (ms / 1000), 1) if ms else None,
                "cer_raw": round(levenshtein(content_chars(raw), content_chars(s["text"]))
                                 / max(1, len(content_chars(s["text"]))), 4),
                "cer_corrected": round(levenshtein(content_chars(corrected), content_chars(s["text"]))
                                       / max(1, len(content_chars(s["text"]))), 4),
                "changes": len(changes),
            })
            print(f"  [{s['start']//60:02d}:{s['start']%60:02d}] {dur:4.0f}s → {ms:>6}ms "
                  f"(RTF {dur/(ms/1000):5.1f}x) cer_raw={results[model][-1]['cer_raw']:.3f}")

    # ---- 彙總 ----
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"# open-dictate asr-bench 報告 {ts}", "",
             f"- 語料：{wav.name} 中 {args.speaker} 獨白 {len(segs)} 段共 {total_audio:.0f}s",
             f"- prompt：生產同款（風格種子＋詞庫 150 字＋優先詞表），兩模型同一份",
             "- ⚠️ 參考稿出自 turbo pipeline，CER 對 turbo 有結構性偏向——只當粗訊號；",
             "  客觀軸＝專名命中（raw 層）與延遲 RTF；分歧處看 diff。", "",
             "| 模型 | warmup | CER(raw) | CER(校正後) | 專名命中(raw) | ms/段 p50 | RTF 中位 |",
             "|---|---|---|---|---|---|---|"]
    summary = {}
    ref_terms_all: set[str] = set()
    for s in segs:
        ref_terms_all |= term_hits(s["text"], terms)
    for model in models:
        rs = results[model]
        cer_r = sum(r["cer_raw"] for r in rs) / len(rs)
        cer_c = sum(r["cer_corrected"] for r in rs) / len(rs)
        mss = sorted(r["ms"] for r in rs)
        rtfs = sorted(r["rtf"] for r in rs)
        hits = set()
        for r in rs:
            hits |= term_hits(r["raw"], terms)
        hit_str = f"{len(hits & ref_terms_all)}/{len(ref_terms_all)}"
        summary[model] = {"cer_raw": round(cer_r, 4), "cer_corrected": round(cer_c, 4),
                          "noun_hits": hit_str, "ms_p50": mss[len(mss)//2],
                          "rtf_median": rtfs[len(rtfs)//2], "warmup_s": round(warm[model], 1)}
        lines.append(f"| {model.split('/')[-1]} | {warm[model]:.1f}s | {cer_r:.4f} | {cer_c:.4f} "
                     f"| {hit_str} | {mss[len(mss)//2]} | {rtfs[len(rtfs)//2]}x |")

    lines.append("\n## 專名命中明細（參考稿含有的專名，各模型 raw 輸出有沒有）")
    for t in sorted(ref_terms_all):
        row = f"- `{t}`: "
        row += " / ".join(f"{m.split('/')[-1]} {'✅' if any(t in content_chars(r['raw']) for r in results[m]) else '❌'}"
                          for m in models)
        lines.append(row)

    lines.append("\n## 分歧樣本（校正後，供人眼判）")
    for i, s in enumerate(segs):
        outs = {m: results[m][i]["corrected"] for m in models}
        if len({content_chars(o) for o in outs.values()}) > 1:
            lines.append(f"\n### [{s['start']//60:02d}:{s['start']%60:02d}]（{s['end']-s['start']:.0f}s）")
            lines.append(f"- ref: {s['text']}")
            for m in models:
                lines.append(f"- {m.split('/')[-1]}: {outs[m]}")

    report = "\n".join(lines) + "\n"
    path = EVAL_DIR / f"asr-report-{ts}.md"
    path.write_text(report, encoding="utf-8")
    with open(EVAL_DIR / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "type": "asr",
                            "corpus": {"wav": wav.name, "segments": len(segs),
                                       "audio_s": round(total_audio)},
                            "models": summary}, ensure_ascii=False) + "\n")
    print(f"\n報告 → {path}\n")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
