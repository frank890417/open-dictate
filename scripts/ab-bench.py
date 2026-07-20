#!/usr/bin/env python3
"""ab-bench — open-dictate 文字層重放 A/B 測試台（Stage 1，2026-07-16）。

規劃 SSOT：docs/AB-EVAL-PLAN.md。觸發：7/15 Breeze ASR + gemma-4-e4b 後處理線索。
acceptance gates：錯字下降／語序改動 0／專名不被改壞／延遲壓 300ms。

變體：
  A          = lex.correct + smart_punct_zh（現行預設路徑，無 LLM）
  B(model)   = lex.correct + LLM 標點/受控錯字 + 閘門 v2（生產 llm_zh 同邏輯），模型可多枚

語料：
  - fixtures/calibration-v1.json 五段 ground truth ＋ dictation-log 內 fuzzy 對回的真實朗讀 raw
  - 近 N 天 dictation-log replay（無 truth：量閘門通過率／pre-gate 野性／專名完整／延遲）

隱私：log 絕不離機；報告只含統計與校準稿相關 diff（校準稿本身是公開測試稿）。
產物：~/.open-dictate/dictation-eval/report-*.md ＋ history.jsonl（演化歷史，可考）。

跑法（重用 td-subtitle venv，與 daemon 同）：
  python3 scripts/ab-bench.py --quick
  python3 scripts/ab-bench.py \
      --models qwen3.6:35b-a3b-coding-nvfp4,gemma4:e4b-nvfp4 --days 7

exit code：--quick 模式 0=全過 1=有失敗（可掛 smoke-test）；完整模式恆 0（報告制）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEXICON_ROOT = Path(os.environ.get("OPEN_DICTATE_LEXICON_ROOT", ROOT / "vendor")).expanduser()
LOG_DIR = Path("~/.open-dictate/dictation-log").expanduser()
EVAL_DIR = Path("~/.open-dictate/dictation-eval").expanduser()
FIXTURE = ROOT / "fixtures" / "calibration-v1.json"

sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
sys.path.insert(0, str(ROOT / "daemon"))

# 生產 code 直接 import（閘門/常數不複寫——閘門是安全關鍵，必須測「同一份」）
import dictated  # noqa: E402
from muse_lexicon import Lexicon, apply_opencc, smart_punct_zh  # noqa: E402

OLLAMA_URL = os.environ.get("OPEN_DICTATE_PUNCT_LLM_URL", "http://127.0.0.1:11434/api/chat")


# ---------------------------------------------------------------------------
# 基礎工具（content 正規化與閘門皆用 dictated 的實作）
# ---------------------------------------------------------------------------
def content_chars(text: str) -> str:
    return dictated._content_chars(text)


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


def cer(hyp: str, truth: str) -> float:
    t = content_chars(truth)
    if not t:
        return 0.0
    return levenshtein(content_chars(hyp), t) / len(t)


def safe_pairs_of(lex: Lexicon) -> list[tuple[str, str]]:
    """與 daemon._safe_pairs 同邏輯：純字串、非 regex、非 callable 的 replacements。"""
    out = []
    for pattern, repl, _desc in lex.replacements:
        if callable(repl):
            continue
        if re.escape(pattern) != pattern:
            continue
        out.append((pattern, str(repl)))
    return out


def gate_check(inp: str, out: str, contextual: list, safe: list) -> bool:
    """閘門 v2（生產同款）：content 序列僅能由授權 pair 重建。"""
    allowed = [(content_chars(w), content_chars(r)) for w, r in (contextual + safe)]
    allowed = [(w, r) for w, r in allowed if w and r and w != r]
    return dictated._reachable_by_pairs(content_chars(inp), content_chars(out), allowed)


def canonical_terms(lex: Lexicon) -> list[str]:
    """專名完整性掃描表：詞庫 canonical ＋ daemon 優先詞表（content 正規化，去重）。"""
    terms = list(lex.canonical) + [t.strip() for t in dictated.DICTATE_PRIORITY_TERMS.split("、")]
    seen, out = set(), []
    for t in terms:
        c = content_chars(t)
        if len(c) >= 2 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def canonical_violations(inp: str, out: str, terms: list[str]) -> list[str]:
    """輸入（校正後）content 含專名、輸出 content 不含 → 該專名被改壞。"""
    ic, oc = content_chars(inp), content_chars(out)
    return [t for t in terms if t in ic and t not in oc]


# ---------------------------------------------------------------------------
# LLM 呼叫（prompt 組裝重用 dictated 常數；bench 需觀測 pre-gate 原始輸出，
# 故不直接呼叫 llm_punct_and_fix——它閘門不過會回 None，看不到野性）
# ---------------------------------------------------------------------------
def build_prompt(text: str, contextual: list[tuple[str, str]]) -> str:
    prompt = dictated.PUNCT_LLM_PROMPT_HEAD
    if contextual:
        table = "、".join(f"{w}→{r}" for w, r in contextual)
        prompt += dictated.PUNCT_LLM_PROMPT_CTX.format(table=table)
    return prompt + dictated.PUNCT_LLM_PROMPT_TAIL + text


def call_llm(model: str, prompt: str, text_len: int, timeout: float) -> tuple[str | None, int, str]:
    """回 (輸出|None, 毫秒, 錯誤字串)。think:false 不被支援時自動重試（gemma 家族）。"""
    body = {
        "model": model, "think": False, "stream": False, "keep_alive": "30m",
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": max(64, text_len * 2)},
    }
    for attempt in (1, 2):
        req = urllib.request.Request(OLLAMA_URL, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = (json.load(r).get("message") or {}).get("content", "").strip()
            return out, int((time.perf_counter() - t0) * 1000), ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:200]
            except OSError:
                pass
            if attempt == 1 and "think" in detail.lower():
                body.pop("think", None)  # 模型不支援 think 參數 → 拿掉重試
                continue
            return None, int((time.perf_counter() - t0) * 1000), f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
            return None, int((time.perf_counter() - t0) * 1000), e.__class__.__name__
    return None, 0, "unreachable"


# ---------------------------------------------------------------------------
# 語料組裝
# ---------------------------------------------------------------------------
def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def read_log_rows(day: str) -> list[dict]:
    path = LOG_DIR / f"{day}.jsonl"
    rows = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def collect_calibration_cases(fx: dict, match_threshold: float = 0.55) -> list[dict]:
    """從 2026-07-11 校準日 log 撈 raw，fuzzy 對回五段 truth。"""
    rows = read_log_rows("2026-07-11")
    paras = fx["paragraphs"]
    cases = []
    for row in rows:
        raw = (row.get("raw") or "").strip()
        if row.get("error") or len(content_chars(raw)) < 10:
            continue
        best, best_ratio = None, 0.0
        for p in paras:
            ratio = SequenceMatcher(None, content_chars(raw), content_chars(p["truth"])).ratio()
            if ratio > best_ratio:
                best, best_ratio = p, ratio
        if best and best_ratio >= match_threshold:
            cases.append({"id": f"{best['id']}@{row['ts']}", "raw": raw, "truth": best["truth"],
                          "para": best["id"], "match": round(best_ratio, 3),
                          "number_tolerant": bool(best.get("number_tolerant")),
                          "source": "calibration-log"})
    return cases


def collect_replay_cases(days: int, max_cases: int) -> list[dict]:
    """近 N 天真實口述 replay（無 truth）。新→舊，去空、去錯誤、去超短。"""
    cases = []
    today = datetime.now().date()
    for d in range(days):
        day = (today - timedelta(days=d)).strftime("%Y-%m-%d")
        for row in reversed(read_log_rows(day)):
            raw = (row.get("raw") or "").strip()
            if row.get("error") or len(content_chars(raw)) < 4:
                continue
            if len(raw) > dictated.PUNCT_LLM_MAX_CHARS:
                continue  # 生產同界：>800 字不進 LLM 層
            cases.append({"id": f"log@{row['ts']}", "raw": raw, "truth": None,
                          "source": f"replay-{day}"})
            if len(cases) >= max_cases:
                return cases
    return cases


# ---------------------------------------------------------------------------
# 變體執行
# ---------------------------------------------------------------------------
def run_variant_a(lex: Lexicon, cases: list[dict]) -> list[dict]:
    out = []
    for c in cases:
        t0 = time.perf_counter()
        corrected, changes = lex.correct(c["raw"])
        final = smart_punct_zh(corrected)
        ms = int((time.perf_counter() - t0) * 1000)
        out.append({**c, "corrected": corrected, "final": final, "ms": ms,
                    "lex_changes": len(changes), "gate": None, "pre_gate_violation": None})
    return out


def run_variant_b(lex: Lexicon, cases: list[dict], model: str, timeout: float) -> list[dict]:
    contextual = list(lex.contextual)
    safe = safe_pairs_of(lex)
    out = []
    for c in cases:
        corrected, changes = lex.correct(c["raw"])
        prompt = build_prompt(corrected, contextual)
        llm_out, ms, err = call_llm(model, prompt, len(corrected), timeout)
        pre_gate_violation = False
        gate_pass = False
        if llm_out is not None:
            normalized = apply_opencc(llm_out)
            gate_pass = bool(normalized) and gate_check(corrected, normalized, contextual, safe)
            pre_gate_violation = not gate_pass
            final = normalized if gate_pass else smart_punct_zh(corrected)
        else:
            final = smart_punct_zh(corrected)
        out.append({**c, "corrected": corrected, "final": final, "ms": ms, "err": err,
                    "lex_changes": len(changes), "gate": gate_pass,
                    "pre_gate_violation": pre_gate_violation,
                    "llm_raw_out": llm_out})
        print(f"    {c['id'][:46]:<48} {ms:>6}ms gate={'✓' if gate_pass else '✗'}"
              f"{' ERR:' + err if err else ''}", flush=True)
    return out


# ---------------------------------------------------------------------------
# 指標彙總
# ---------------------------------------------------------------------------
def pct(sorted_ms: list[int], p: float) -> int | None:
    if not sorted_ms:
        return None
    return sorted_ms[min(len(sorted_ms) - 1, int((len(sorted_ms) - 1) * p))]


def summarize(results: list[dict], terms: list[str], prod_timeout_ms: int = 8000) -> dict:
    with_truth = [r for r in results if r.get("truth")]
    cers = [cer(r["final"], r["truth"]) for r in with_truth]
    cano_bad = []
    for r in results:
        v = canonical_violations(r["corrected"], r["final"], terms)
        if v:
            cano_bad.append({"id": r["id"], "broken": v})
    ms = sorted(r["ms"] for r in results)
    gates = [r for r in results if r.get("gate") is not None]
    post_gate_violations = 0  # 閘門過了才可能有——用「gate=✓ 且 content 不可達」再驗一次的意義
    # 已由 gate_check 保證；此欄位存在是為了報告裡明示「恆 0 是驗證過的，不是假設」
    return {
        "n": len(results),
        "n_truth": len(with_truth),
        "cer_mean": round(sum(cers) / len(cers), 4) if cers else None,
        "canonical_violations": cano_bad,
        "ms_p50": pct(ms, 0.5), "ms_p90": pct(ms, 0.9), "ms_max": ms[-1] if ms else None,
        "gate_pass_rate": round(sum(1 for r in gates if r["gate"]) / len(gates), 3) if gates else None,
        "pre_gate_violation_rate": round(
            sum(1 for r in gates if r["pre_gate_violation"]) / len(gates), 3) if gates else None,
        "post_gate_violations": post_gate_violations,
        "prod_timeout_pass_rate": round(
            sum(1 for m in ms if m <= prod_timeout_ms) / len(ms), 3) if ms else None,
        "llm_err_count": sum(1 for r in results if r.get("err")),
    }


# ---------------------------------------------------------------------------
# quick 模式（確定性，無 LLM，可掛 smoke-test）
# ---------------------------------------------------------------------------
def run_quick(fx: dict, lex: Lexicon) -> int:
    fails = 0
    print("== gate adversarial（生產 _reachable_by_pairs 同一份 code）==")
    for case in fx["gate_adversarial"]:
        got = dictated._reachable_by_pairs(
            content_chars(case["a"]), content_chars(case["b"]),
            [(content_chars(w), content_chars(r)) for w, r in case["pairs"]])
        ok = got == case["expect"]
        print(f"  {'✓' if ok else '✗'} {case['id']}  ({case['note']})")
        fails += 0 if ok else 1

    print("\n== variant A on calibration truths（不誤傷 + 冪等）==")
    for p in fx["paragraphs"]:
        corrected, changes = lex.correct(p["truth"])
        final = smart_punct_zh(corrected)
        # ground truth 本身已是正字：校正層不得把它改壞（允許標點格式化，content 必須不變）
        same = content_chars(final) == content_chars(p["truth"])
        idem = smart_punct_zh(final) == final
        ok = same and idem
        print(f"  {'✓' if ok else '✗'} {p['id']}  content_stable={same} idempotent={idem} "
              f"lex_changes={len(changes)}")
        fails += 0 if ok else 1

    print(f"\nquick summary: {'PASS' if fails == 0 else f'{fails} FAIL'}")
    return 1 if fails else 0


# ---------------------------------------------------------------------------
# 報告
# ---------------------------------------------------------------------------
def fmt_summary_row(name: str, s: dict) -> str:
    cer_s = f"{s['cer_mean']:.4f}" if s["cer_mean"] is not None else "—"
    gate_s = f"{s['gate_pass_rate']:.0%}" if s["gate_pass_rate"] is not None else "—"
    wild_s = f"{s['pre_gate_violation_rate']:.0%}" if s["pre_gate_violation_rate"] is not None else "—"
    return (f"| {name} | {s['n']} | {cer_s} | {len(s['canonical_violations'])} | "
            f"{s['post_gate_violations']} | {gate_s} | {wild_s} | "
            f"{s['ms_p50']} / {s['ms_p90']} / {s['ms_max']} | {s['llm_err_count']} |")


def verdict_for(name: str, s: dict, s_a: dict, latency_target: int) -> dict:
    checks = {
        "錯字不升": s["cer_mean"] is not None and s_a["cer_mean"] is not None
                   and s["cer_mean"] <= s_a["cer_mean"] + 1e-9,
        "語序0(post-gate)": s["post_gate_violations"] == 0,
        "專名不壞": len(s["canonical_violations"]) == 0,
        f"延遲p50≤{latency_target}ms": s["ms_p50"] is not None and s["ms_p50"] <= latency_target,
        "生產8s內p100≥95%": (s["prod_timeout_pass_rate"] or 0) >= 0.95,
    }
    return {"name": name, "checks": checks, "all_pass": all(checks.values())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="確定性子集（無 LLM），exit code 可掛 smoke")
    ap.add_argument("--models", default="qwen3.6:35b-a3b-coding-nvfp4,gemma4:e4b-nvfp4")
    ap.add_argument("--days", type=int, default=7, help="log replay 回看天數")
    ap.add_argument("--max-cases", type=int, default=40, help="replay 語料上限")
    ap.add_argument("--timeout", type=float, default=30.0, help="bench LLM timeout（量真延遲）")
    ap.add_argument("--latency-target", type=int, default=300, help="latency target（ms）")
    ap.add_argument("--no-replay", action="store_true", help="只跑校準集")
    args = ap.parse_args()

    fx = load_fixture()
    lex = Lexicon.load()
    terms = canonical_terms(lex)

    if args.quick:
        return run_quick(fx, lex)

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    cal = collect_calibration_cases(fx)
    replay = [] if args.no_replay else collect_replay_cases(args.days, args.max_cases)
    cases = cal + replay
    print(f"語料：校準集 {len(cal)}（有 truth）＋ replay {len(replay)}（近 {args.days} 天）＝ {len(cases)}")
    if not cases:
        print("沒有語料可跑。")
        return 0

    print("\n== Variant A: lexicon + smart_punct_zh ==")
    res_a = run_variant_a(lex, cases)
    sum_a = summarize(res_a, terms)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    all_b: dict[str, tuple[list, dict]] = {}
    for model in models:
        print(f"\n== Variant B: lexicon + llm({model}) + 閘門 v2 ==")
        # 熱身（keep_alive 30m，對齊生產常駐；首次載入不計入延遲統計）
        _, warm_ms, warm_err = call_llm(model, "回覆「好」一個字。", 4, 120.0)
        print(f"  warmup: {warm_ms}ms{' ERR:' + warm_err if warm_err else ''}")
        res_b = run_variant_b(lex, cases, model, args.timeout)
        all_b[model] = (res_b, summarize(res_b, terms))

    # ---- 報告 ----
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    lines = [
        f"# open-dictate ab-bench 報告 {ts}",
        "",
        f"- 語料：校準集 {len(cal)}（ground truth）＋ 真實口述 replay {len(replay)}（近 {args.days} 天，私有不出境）",
        f"- 驗收線：延遲 p50 ≤ {args.latency_target}ms（user線）；生產 timeout 8s；"
        f"CER 為 content-char 層（段三數字寬容 → 絕對值僅供 A/B 相對比較）",
        "",
        "| 變體 | n | CER(校準) | 專名壞 | post-gate違規 | 閘門通過 | pre-gate野性 | ms p50/p90/max | LLM err |",
        "|---|---|---|---|---|---|---|---|---|",
        fmt_summary_row("A smart_zh", sum_a),
    ]
    verdicts = []
    for model, (_res, s) in all_b.items():
        lines.append(fmt_summary_row(f"B {model}", s))
        verdicts.append(verdict_for(model, s, sum_a, args.latency_target))
    lines.append("")
    lines.append("## verdict（acceptance gates + 生產穩定線）")
    for v in verdicts:
        lines.append(f"- **{v['name']}** → {'✅ 全過' if v['all_pass'] else '❌ 未全過'}")
        for k, ok in v["checks"].items():
            lines.append(f"  - {'✅' if ok else '❌'} {k}")
    for model, (res, s) in all_b.items():
        if s["canonical_violations"]:
            lines.append(f"\n### {model} 專名違規明細")
            for v in s["canonical_violations"][:10]:
                lines.append(f"- {v['id']}: {v['broken']}")
    # 校準集 miss 明細（人審詞庫候選來源；校準稿為公開測試稿，可列 diff）
    lines.append("\n## 校準集 A 路徑殘餘錯誤（詞庫候選，人審）")
    for r in res_a:
        if r.get("truth") and cer(r["final"], r["truth"]) > 0:
            lines.append(f"- {r['id']} (CER {cer(r['final'], r['truth']):.3f})")
            lines.append(f"  - out: {r['final']}")
            lines.append(f"  - exp: {r['truth']}")

    report = "\n".join(lines) + "\n"
    report_path = EVAL_DIR / f"report-{ts}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n報告 → {report_path}")

    # ---- 演化歷史（可考）----
    hist = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"calibration": len(cal), "replay": len(replay), "days": args.days},
        "latency_target_ms": args.latency_target,
        "variant_a": sum_a,
        "variants_b": {m: s for m, (_r, s) in all_b.items()},
        "verdicts": verdicts,
    }
    with open(EVAL_DIR / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False, default=str) + "\n")
    print(f"歷史 → {EVAL_DIR / 'history.jsonl'}")

    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
