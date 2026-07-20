# Open Dictate Meeting, Speaker Identity, Self-Evolution, and Bilingual README Plan

> **For Hermes:** This is a planning document. Do not implement all features at once. Execute phase by phase, with public-safety checks after every phase.

**Goal:** Expand Open Dictate from a push-to-talk dictation app into a local-first Traditional Chinese dictation and meeting transcription toolkit with optional speaker identity, post-transcription mishearing detection, review-first self-evolving glossaries, and bilingual Traditional Chinese + English documentation.

**Architecture:** Keep the current real-time dictation core small and stable. Add meeting transcription, speaker identity, and self-evolution as optional layers with strict local-only data boundaries. Public repo contains interfaces, schemas, fictional fixtures, deterministic safety gates, and docs; user-owned audio, speaker profiles, transcripts, review queues, and glossary growth live under `~/.open-dictate/` or user-selected local paths and are never committed.

**Tech Stack:** Swift Package Manager, macOS launchd, Python 3, MLX Whisper, OpenCC, optional local Ollama, optional diarization/speaker-embedding backend, GitHub Actions.

---

## 0. Product Frame

Open Dictate should not be “just a Whisper wrapper.” The public product should be:

> **Open Dictate is a local-first dictation and meeting transcription toolkit for macOS, optimized for Traditional Chinese, deterministic correction, speaker-aware transcripts, and privacy-preserving personal glossary growth.**

中文：

> **Open Dictate 是為 macOS 打造的本地優先語音輸入與會議轉錄工具，針對繁體中文、確定性校正、說話者辨識，以及可審核的個人詞庫成長而設計。**

The differentiator is the loop:

```text
Speak / record
  → local transcription
  → deterministic correction
  → speaker-aware transcript
  → automatic mishearing candidates
  → human review
  → personal glossary improves
  → next transcription gets better
```

Do **not** describe it as “AI rewrites your transcript.” The rule is the opposite: **no silent rewriting**.

---

## 1. What Should Be Public vs Local-Only

### Public repo may contain

- Interfaces and schemas.
- Fictional text/audio fixtures.
- Anonymous speaker examples.
- Starter Traditional Chinese glossary.
- No-rewrite gate and deterministic QA logic.
- CLI commands and docs.
- Example configs with fake names.
- Tests using generated or fictional data.

### Public repo must not contain

- Real dictation logs.
- Real meeting transcripts.
- Real audio files.
- Real speaker embeddings / voiceprints.
- Real speaker name mappings.
- Private glossary accumulated from daily use.
- Obsidian or private Muse/Hermes routing.
- Private project names, collaborator names, schedules, or internal paths.

### Local-only default data roots

```text
~/.open-dictate/dictation-log/
~/.open-dictate/meetings/
~/.open-dictate/glossaries/
~/.open-dictate/review-queue/
~/.open-dictate/speakers/
~/.open-dictate/eval/
```

These paths must be in `.gitignore` and public-safety scan rules.

---

## 2. Target Architecture

```text
OpenDictate.app
  ├─ Real-time dictation UI
  ├─ Teaching UI for wrong → right glossary pairs
  └─ Future review queue UI

Python services / CLI
  ├─ daemon/dictated.py              # current low-latency dictation daemon
  ├─ daemon/meeting_cli.py           # meeting transcription entrypoint
  ├─ daemon/pipeline/meeting.py      # chunk → ASR → correction → QA → export
  ├─ daemon/speaker/base.py          # speaker adapter interface
  ├─ daemon/speaker/anonymous.py     # default anonymous speaker labels
  ├─ daemon/speaker/local_profiles.py# optional local voiceprint profiles
  ├─ daemon/qa/mishear_detector.py   # risk flags, not direct replacement
  ├─ daemon/qa/no_rewrite_gate.py    # content preservation gate
  ├─ daemon/glossary/review_queue.py # accept/reject/undo candidates
  └─ daemon/exporters/               # md/srt/vtt/jsonl
```

Core principle:

- Real-time dictation remains fast.
- Meeting mode can be slower and richer.
- Speaker identity is optional.
- Self-evolution is review-first.

---

## 3. Feature Layers

## 3.1 Layer A — Real-time Dictation Core

Status: already public seed.

Keep:

- Push-to-talk.
- MLX Whisper keep-warm daemon.
- `smart_zh`, `raw`, optional `llm_zh`.
- Deterministic glossary correction.
- Menubar teaching flow.

Needed cleanup before larger expansion:

- Rename internal `muse_lexicon` to a public name later, e.g. `open_lexicon`, but not in the same phase as meeting/speaker work.
- Keep backward compatibility or provide migration shim.
- Make sure docs say “starter glossary” not “personal private glossary.”

---

## 3.2 Layer B — Meeting Mode

Goal: long audio → corrected meeting transcript.

Minimal public MVP:

```bash
python3 daemon/meeting_cli.py transcribe input.wav --out ~/.open-dictate/meetings/demo/
```

Pipeline:

```text
input audio/video
  → ffmpeg normalize to 16k mono wav
  → chunking or VAD
  → MLX Whisper ASR
  → deterministic glossary correction
  → QA flags
  → export Markdown / SRT / VTT / JSONL
```

Output schema:

```json
{
  "meeting_id": "local-id",
  "created_at": "2026-07-20T00:00:00Z",
  "language": "zh-TW",
  "segments": [
    {
      "start": 12.34,
      "end": 18.90,
      "speaker": "SPEAKER_01",
      "raw": "原始轉錄",
      "text": "校正後文字",
      "changes": [["誤聽", "正確"]],
      "qa_flags": []
    }
  ]
}
```

Non-goals for MVP:

- No Obsidian export.
- No private meeting analysis templates.
- No automatic real-name speaker assignment.

---

## 3.3 Layer C — Speaker Separation and Speaker Identity

This is useful but sensitive. Split it into two levels.

### C1 — Anonymous speaker diarization

Default public behavior:

```text
SPEAKER_00
SPEAKER_01
SPEAKER_02
```

No names, no identity claims.

### C2 — Optional local speaker profiles

Local-only user flow:

```bash
open-dictate speaker enroll --id alice --display-name "Alice" --audio alice_sample.wav
open-dictate speaker identify meeting.wav
open-dictate speaker list
```

Local profile schema:

```json
{
  "schema_version": "0.1",
  "speaker_id": "alice",
  "display_name": "Alice",
  "embedding_model": "local-model-name",
  "embedding": [0.0123, -0.0456],
  "created_at": "2026-07-20T00:00:00Z",
  "consent": {
    "source": "user-provided",
    "notes": "local-only"
  }
}
```

Rules:

- Never commit speaker profiles.
- Never ship real voiceprints.
- Use fake embeddings in tests.
- Docs must call speaker embeddings biometric/sensitive data.
- If confidence is low, output “unknown speaker,” not a guessed name.

---

## 3.4 Layer D — Post-transcription Mishearing Detection

This is the real product edge.

The system should scan a completed dictation or meeting transcript and produce reviewable candidates:

```json
{
  "type": "possible_mishear",
  "surface": "阿布西店",
  "candidate": "Obsidian",
  "confidence": 0.61,
  "reason": "near canonical glossary term",
  "action": "review"
}
```

Detection signals:

1. Near match to `_canonical` terms.
2. Repeated ASR hallucination patterns.
3. Suspicious mixed Chinese/English term boundaries.
4. Segment around speaker change.
5. Low ASR confidence if available.
6. `llm_zh` rejected by no-rewrite gate.
7. Number/date/money-like spans that should not be silently changed.
8. High edit distance between raw and corrected text.

Hard rule:

> QA may flag. QA may suggest. QA must not silently write into `replacements`.

---

## 3.5 Layer E — Review-first Self-evolving Glossary

The public feature should be “self-evolving with review,” not “self-mutating.”

Commands:

```bash
open-dictate qa scan ~/.open-dictate/meetings/demo/transcript.jsonl
open-dictate glossary candidates
open-dictate glossary accept <candidate-id>
open-dictate glossary reject <candidate-id>
open-dictate glossary undo <change-id>
```

Glossary schema v0.2 proposal:

```json
{
  "_meta": {
    "version": "0.2.0",
    "privacy": "local-only"
  },
  "replacements": {
    "錯誤詞": "正確詞"
  },
  "_canonical": [
    "Open Dictate",
    "TouchDesigner"
  ],
  "_contextual": [
    {
      "wrong": "來彈",
      "right": "來談",
      "auto_apply": false
    }
  ],
  "_review_queue": [
    {
      "id": "cand_001",
      "wrong": "阿布西店",
      "candidate": "Obsidian",
      "source": "meeting-qa",
      "status": "pending",
      "examples": []
    }
  ],
  "_history": []
}
```

Auto-apply categories:

- `replacements`: deterministic, high confidence, low risk.
- `_contextual`: only allowed through a protected local model gate; not regex-applied.
- `_review_queue`: never auto-applied.
- `_rejected`: do not suggest repeatedly unless new evidence is strong.

---

## 4. Bilingual README Plan

README should become bilingual, not English-only.

Recommended structure:

```markdown
# Open Dictate 🎙️

> 本地優先的 macOS 繁體中文語音輸入與會議轉錄工具。  
> Local-first Traditional Chinese dictation and meeting transcription for macOS.

## 快速介紹 / TL;DR

## 為什麼做這個 / Why Open Dictate

## 功能 / Features

## 隱私模型 / Privacy Model

## 快速開始 / Quick Start

## 架構 / Architecture

## 自我進化詞庫 / Self-evolving Glossary

## 會議模式 / Meeting Mode

## 說話者辨識 / Speaker Identity

## 路線圖 / Roadmap

## 建置與測試 / Build and Test

## 貢獻 / Contributing
```

Status labels:

| Feature | Status |
|---|---|
| Push-to-talk dictation | Stable public seed |
| Deterministic glossary correction | Stable public seed |
| Meeting transcription | Planned / next |
| Post-transcription QA | Planned / next |
| Review-first glossary growth | Planned / next |
| Anonymous speaker diarization | Experimental planned |
| Local speaker identity | Sensitive optional planned |

Tone:

- Traditional Chinese first.
- English mirrors the same safety claims.
- Avoid overclaiming unfinished features.
- Make privacy model visible near the top.

---

## 5. Phase Plan

### Phase 0 — Safety Foundation

**Objective:** Make repo safe before adding meeting/speaker data surfaces.

Tasks:

1. Expand `.gitignore` for:
   - `*.wav`, `*.m4a`, `*.mp3`, `*.flac`, `*.aiff`
   - `*.npy`, `*.pt`, `*.pkl`
   - `speaker_profiles*.json`
   - `voiceprints/`
   - `transcripts/`
   - `meetings/`
   - `review-queue/`
   - `~/.open-dictate` examples never copied into repo
2. Expand `scripts/public-safety-scan.py` for speaker and meeting artifacts.
3. Add `docs/PRIVACY.md`.
4. Update `CONTRIBUTING.md` with “no private audio/transcripts/voiceprints in issues or PRs.”

Verification:

```bash
python3 scripts/public-safety-scan.py
python3 -m compileall daemon scripts vendor
python3 scripts/golden-bench.py --skip-daemon
./scripts/smoke-test.sh
```

Commit:

```bash
git commit -m "chore: strengthen privacy guardrails"
```

---

### Phase 1 — Bilingual README and Roadmap

**Objective:** Present the full vision before implementation.

Tasks:

1. Rewrite README as Traditional Chinese + English.
2. Add feature status table.
3. Add architecture diagram that includes planned meeting/QA/speaker layers.
4. Add roadmap section.
5. Keep screenshot.

Verification:

```bash
python3 scripts/public-safety-scan.py
```

Commit:

```bash
git commit -m "docs: make README bilingual and add roadmap"
```

---

### Phase 2 — Meeting Mode MVP

**Objective:** Long audio to corrected transcript, no speaker identity yet.

Files to create:

```text
daemon/meeting_cli.py
daemon/pipeline/meeting.py
daemon/exporters/markdown.py
daemon/exporters/srt.py
daemon/exporters/jsonl.py
tests/fixtures/meeting_public.json
docs/MEETING.md
```

Core command:

```bash
python3 daemon/meeting_cli.py transcribe fixtures/audio/public-demo.wav --out /tmp/open-dictate-meeting-demo
```

MVP may use synthetic/public generated audio only.

Verification:

```bash
python3 -m compileall daemon scripts vendor
python3 daemon/meeting_cli.py --help
python3 daemon/meeting_cli.py export-demo --out /tmp/open-dictate-demo
python3 scripts/public-safety-scan.py
```

Commit:

```bash
git commit -m "feat: add meeting transcription MVP"
```

---

### Phase 3 — Mishearing Detection MVP

**Objective:** Scan transcripts and create reviewable candidates.

Files to create:

```text
daemon/qa/no_rewrite_gate.py
daemon/qa/mishear_detector.py
daemon/qa/review_schema.py
tests/test_no_rewrite_gate.py
tests/test_mishear_detector.py
docs/SELF-EVOLVING-GLOSSARY.md
```

Verification:

```bash
python3 -m unittest discover tests
python3 scripts/public-safety-scan.py
```

Commit:

```bash
git commit -m "feat: add mishearing QA scanner"
```

---

### Phase 4 — Review Queue and Glossary Growth

**Objective:** Human-approved self-evolution.

Files to create/modify:

```text
daemon/glossary/review_queue.py
daemon/glossary/cli.py
vendor/tools/td-subtitle/glossaries/personal.example.json
README.md
```

Commands:

```bash
python3 daemon/glossary/cli.py candidates
python3 daemon/glossary/cli.py accept cand_001
python3 daemon/glossary/cli.py reject cand_002
python3 daemon/glossary/cli.py undo change_001
```

Verification:

```bash
python3 -m unittest discover tests
python3 scripts/golden-bench.py --skip-daemon
```

Commit:

```bash
git commit -m "feat: add review-first glossary growth"
```

---

### Phase 5 — Anonymous Speaker Layer

**Objective:** Add speaker labels without identifying people.

Files to create:

```text
daemon/speaker/base.py
daemon/speaker/anonymous.py
daemon/speaker/schema.py
tests/test_speaker_anonymous.py
docs/SPEAKER-ID.md
```

Output default:

```text
SPEAKER_00
SPEAKER_01
```

Verification:

```bash
python3 -m unittest discover tests
python3 scripts/public-safety-scan.py
```

Commit:

```bash
git commit -m "feat: add anonymous speaker layer"
```

---

### Phase 6 — Optional Local Speaker Identity

**Objective:** Let users enroll speakers locally, with clear biometric warnings.

Files to create:

```text
daemon/speaker/local_profiles.py
daemon/speaker/cli.py
examples/speaker_profiles.example.json
docs/SPEAKER-ID.md
```

Commands:

```bash
python3 daemon/speaker/cli.py enroll --id alice --audio /path/to/local.wav
python3 daemon/speaker/cli.py list
python3 daemon/speaker/cli.py identify /path/to/segment.wav
```

Verification:

```bash
python3 scripts/public-safety-scan.py
python3 -m unittest discover tests
```

No real voiceprints in repo.

Commit:

```bash
git commit -m "feat: add optional local speaker profiles"
```

---

## 6. Testing Strategy

### Unit tests

- glossary load
- deterministic replacement
- punctuation idempotence
- no-rewrite gate
- QA candidate generation
- review queue accept/reject/undo
- anonymous speaker labels
- export formats

### Golden tests

Use fictional Traditional Chinese samples only.

### Privacy tests

`public-safety-scan.py` must fail on:

- private paths
- voiceprints
- speaker profiles
- real audio files
- transcript logs
- private project keywords
- suspicious credentials

### Integration tests

- fake daemon
- synthetic WAV
- meeting JSON export
- Markdown export
- SRT/VTT export

### CI

Keep the current Python + Swift jobs. Add tests once `tests/` exists.

---

## 7. Risk Register

| Risk | Why it matters | Mitigation |
|---|---|---|
| Speaker embeddings are biometric data | Can identify real people | Local-only, git-ignored, docs warning, fake tests |
| Self-evolving glossary learns wrong pairs | One bad pair pollutes all future transcripts | Review-first, undo, contextual bucket, visible changes |
| LLM punctuation rewrites content | Violates trust | No-rewrite gate, fallback to deterministic mode |
| Meeting mode leaks private workflow | Public repo should not encode Muse/Obsidian internals | Generic exporters only; integrations as external adapters |
| README overclaims planned features | Users lose trust | Status labels: stable / planned / experimental |
| Diarization dependencies are heavy | Core install becomes fragile | Optional extras; lazy imports; separate docs |

---

## 8. Suggested Immediate Next Step

Do **Phase 0 + Phase 1 first**:

1. Strengthen privacy guardrails.
2. Rewrite README bilingual with roadmap and full-system vision.
3. Do not implement speaker identity yet.

This gives the project the right public shape without rushing sensitive biometric features into a new repo.

After that, build the product in this order:

```text
Meeting MVP
  → Mishearing QA
  → Review-first glossary growth
  → Anonymous speaker layer
  → Optional local speaker identity
```

---

## 9. Definition of Done for Phase 0 + 1

- README is bilingual Traditional Chinese + English.
- README clearly distinguishes stable vs planned features.
- Privacy model is visible near the top.
- `docs/PRIVACY.md` exists.
- `PUBLICATION-CHECKLIST.md` covers audio/transcripts/voiceprints.
- `public-safety-scan.py` catches speaker and meeting artifacts.
- CI passes.
- No private data or private names appear.
