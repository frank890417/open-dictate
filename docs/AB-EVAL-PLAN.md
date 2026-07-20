# Open Dictate Evaluation Plan

This document defines public, repeatable checks for dictation quality without using private voice logs.

## Goals

- Lower character error rate on Traditional Chinese samples.
- Preserve word order and sentence content.
- Avoid damaging proper nouns and mixed English/Chinese text.
- Keep warm daemon latency low enough for interactive dictation.

## Test Layers

| Layer | Input | Purpose |
|---|---|---|
| L0 | synthetic text fixtures | deterministic punctuation and no-rewrite gate |
| L1 | public calibration text | compare correction behavior across models/settings |
| L2 | user-provided local wav files | optional ASR comparison, never committed |

## Metrics

- CER: character error rate against a provided reference.
- Content gate: non-punctuation content must remain unchanged except authorized glossary pairs.
- Latency: p50/p90 daemon response time.
- Proper noun safety: names in fixture must not be changed.

## Rules

- Do not commit real dictation logs or user audio.
- Store local reports under `~/.open-dictate/eval/`.
- Use fictional/public fixtures for CI.
- Optional local LLM punctuation is accepted only when the no-rewrite gate passes.

## Commands

```bash
python3 scripts/golden-bench.py --skip-daemon
python3 scripts/ab-bench.py --quick
```
