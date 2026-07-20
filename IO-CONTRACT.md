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
{"ok": true, "pong": true, "model": "mlx-community/whisper-large-v3-turbo", "warm": true, "version": "0.5.2"}
{"ok": false, "error": "no_speech"}
```

Error codes: `no_speech`, `file_not_found`, `asr_failed`, `bad_request`, `unknown_cmd`, `add_pair_failed`.

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
