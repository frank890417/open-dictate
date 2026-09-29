# Open Dictate Integration Contract v1.0

This file is the source of truth for the Open Dictate app, daemon, glossary engine, and tests.

## System Overview

```text
[OpenDictate.app] -- wav path --> [daemon/dictated.py] --> mlx_whisper
        ^                              | deterministic correction via muse_lexicon
        |                              v
        +--------- corrected text ---- log: ~/.open-dictate/dictation-log/
```

## Paths

| Item | Default Path | Notes |
|---|---|---|
| Repository | any clone path | `install.sh` resolves paths dynamically |
| Swift app | `OpenDictate/` | Swift Package Manager executable |
| Daemon | `daemon/dictated.py` | Long-running keep-warm process |
| Glossary root | `vendor/` | Override with `OPEN_DICTATE_LEXICON_ROOT` |
| Dictation log | `~/.open-dictate/dictation-log/YYYY-MM-DD.jsonl` | Local only; never commit logs |
| Socket | `/tmp/open-dictate.sock` | Unix domain socket, newline-delimited JSON |
| Python venv | `.venv-dictate/` | Created by standalone install |

## Socket Protocol

Requests:

```json
{"cmd": "transcribe", "wav": "/tmp/open-dictate-rec-20260705-121501.wav", "punct": "smart_zh"}
{"cmd": "ping"}
{"cmd": "reload_lexicon"}
{"cmd": "add_pair", "wrong": "誤聽", "right": "正確", "source": "dictate-ui"}
{"cmd": "stats"}
```

Responses:

```json
{"ok": true, "text": "校正後文字", "raw": "whisper 原始輸出", "changes": [["誤聽", "正確"]], "punct": "smart_zh", "asr_ms": 210, "total_ms": 260}
{"ok": true, "pong": true, "model": "mlx-community/whisper-large-v3-turbo", "warm": true, "version": "0.6.0", "punct_llm": {"enabled": true, "loading": false, "ready_age_s": 12.3, "last_load_s": 8.9, "last_error": null}}
{"ok": false, "error": "no_speech"}
```

Error codes: `no_speech`, `file_not_found`, `asr_failed`, `bad_request`, `unknown_cmd`, `add_pair_failed`.

When the repetition-loop guard removes a runaway tail, the local JSONL log entry
carries an extra `dehall` field describing what was removed. It never appears on the
wire response; the socket contract is unchanged.

⚠️ **Timeout contract**: the shell's `transcribeTimeout()` allows
`max(15, audioSeconds/6 + llmHeadroom)` and the daemon's punctuation budget caps at
`PUNCT_LLM_TIMEOUT_CAP_S`. These two constants are one contract. Raising the cap
without raising the shell headroom produces a false "daemon offline" state while the
daemon actually completes the work in the background. Since daemon 0.6.0 the daemon
also computes the shell deadline itself (formula above minus a 2 s margin) and never
starts or extends an LLM punctuation request past it.

`punct` may also be `llm_zh_partial` (daemon ≥0.5.4): long dictation is punctuated in
chunks and only some chunks passed; the rest used `smart_zh`. Responses may carry
stage timings `pre_asr_ms`, `load_ms`, `lex_ms`, `punct_ms`; `ping` may carry
`punct_llm` (warm-keeper status, daemon ≥0.6.0). Readers ignore unknown fields.

## Audio Format

- 16 kHz, mono, PCM16 WAV.
- Recordings shorter than 0.5 seconds are ignored.
- Temporary wav files are written under `/tmp/` and deleted after processing.

## Glossary Schema

```json
{
  "_meta": {"version": "0.1.0", "description": "starter glossary"},
  "replacements": {"誤聽": "正確"},
  "_review_flagged": {},
  "_canonical": ["正確專名"]
}
```

Only `replacements` are applied automatically. Ambiguous terms should be flagged for human review instead of being corrected blindly.

## Correction Rules

1. Glossary replacements are deterministic.
2. The system should not rewrite or summarize the sentence.
3. Traditional Chinese normalization and punctuation formatting are allowed.
4. Numbers are not semantically changed automatically.
5. Optional local LLM punctuation must pass the content gate: after punctuation is removed, output content must be reachable from input content using only authorized glossary pairs.

## Quality Gates

- Warm transcription of short utterances should target sub-second daemon latency on Apple Silicon.
- `smart_zh` must be deterministic and idempotent.
- No-rewrite gates must reject inserted, deleted, or changed non-punctuation characters unless they match an authorized pair.
- Public fixtures must not contain real user dictation, private names, or private project details.

## Daemon 0.6.0 robustness notes

All changes are daemon-internal; the wire protocol stays 1.0.

- **LLM warm keeper.** A client timeout while Ollama is loading the punctuation model
  cancels the load (`context canceled`), so a cold model never becomes warm. A
  background thread now loads and warms the model with a long timeout and re-checks
  `/api/ps` every `PUNCT_WARM_INTERVAL_S` (120 s). If the model is not resident, a
  request falls back to `smart_zh` immediately instead of waiting out a budget.
  Disable with `<PREFIX>_PUNCT_WARM=0`. Cost: the model stays resident.
- **Contextual table filtering.** Only contextual pairs whose wrong side occurs in the
  current text are sent to the LLM and to the reachability gate. Pairs that do not
  occur cannot be applied, so the gate result is unchanged while the prompt stays small.
- **ASR decode guards.** `sample_len = min(224, ceil(seconds × 12) + 32)`; temperature
  fallback is capped at 0.4 (`(0, 0.4)` below 4 s). High-temperature retries on silence
  produced loops that took 15–41 s for a one-second accidental recording.
- **False-trigger gate.** Recordings under 4 s whose 30 ms-frame dynamic range is below
  9 dB and voiced ratio below 0.08 return `no_speech` without running ASR.
- **Known subtitle-credit hallucinations** (e.g. `MING PAO CANADA`, `字幕由 Amara.org 社群提供`,
  `詞曲 李宗盛`) return `no_speech` when they are the whole output; unambiguous credits
  stuck to the end of real text are trimmed. Output made only of punctuation is `no_speech`.
- **Whisper prompt budget.** mlx-whisper keeps only the last 223 prompt tokens. The prompt
  is now built within 200 tokens with the style sentence at the end; recordings under
  4 s use a terms-only prompt. Optional `<PREFIX>_PROMPT_CORE_TERMS` (`、`-separated)
  are always placed first. Small-form punctuation (`﹐﹖﹗`) is normalized.
- **Sliver windows.** 30–33 s recordings are split at the quietest point near the middle
  via `clip_timestamps`, avoiding a sub-second final window that loops.
- **Silence trimming** of clear leading/trailing silence (≥0.6 s, only when a quiet floor
  exists), and a **prompt-swap retry** when a voiced recording ≥4 s yields implausibly
  little text.
- **TAIL_LOOP backtracking fix.** A run of ~40 separators followed by one character made
  the tail-loop regex backtrack for more than 20 s, blocking the single-threaded daemon.
  Separator runs are now collapsed before matching (cut positions map back to the
  original text).
- Local JSONL log entries may carry diagnostics: `rms`, `dr`, `voiced`,
  `asr_temp_max`, `asr_retry`, `raw_first`, `trim_head_s`, `trim_tail_s`; `dehall`
  may be `speech_gate` or `known_hallucination …`. `no_speech` is a normal outcome,
  not an error.
