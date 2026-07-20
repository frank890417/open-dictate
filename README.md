# Open Dictate 🎙️

> **本地優先的 macOS 繁體中文語音輸入與會議轉錄工具。**
> **Local-first Traditional Chinese dictation and meeting transcription for macOS.**

Hold a hotkey, speak, release, and Open Dictate inserts the transcribed sentence at your cursor. The core path runs locally with MLX Whisper on Apple Silicon, then applies deterministic glossary correction owned by the user.

按住快捷鍵、說話、放開，Open Dictate 會把轉錄後的文字插入目前游標位置。核心流程在 Apple Silicon 上本地執行 MLX Whisper，並套用使用者擁有的確定性詞庫校正。

![Open Dictate screenshot](docs/assets/open-dictate-screenshot.svg)

## 快速介紹 / TL;DR

Open Dictate is not only a Whisper wrapper. The long-term loop is:

Open Dictate 不只是 Whisper 包裝器。完整方向是這個閉環：

```text
Speak / record
  → local transcription
  → deterministic correction
  → speaker-aware transcript
  → possible mishearing candidates
  → human review
  → personal glossary improves
  → next transcription gets better
```

Design rule: **the system may flag and suggest, but it must not silently rewrite meaning.**

設計原則：**系統可以標記、可以建議，但不能靜默改寫意思。**

## 功能狀態 / Feature Status

| 功能 / Feature | 狀態 / Status |
|---|---|
| Push-to-talk dictation / 按住說話輸入 | Public seed |
| Local MLX Whisper daemon / 本地 Whisper 常駐 daemon | Public seed |
| Deterministic glossary correction / 確定性詞庫校正 | Public seed |
| Traditional Chinese punctuation / 繁中全形標點 | Public seed |
| Menubar teaching flow / 選單列教詞庫 | Public seed |
| Meeting transcript package / 會議逐字稿包 | Public seed: audio or JSON/JSONL segments |
| Local audio ASR for Meeting Mode / 會議模式本地音檔 ASR | Public seed: MLX Whisper adapter |
| Post-transcription QA / 轉錄後誤聽偵測 | MVP: review flags |
| Review-first glossary growth / 審核優先詞庫成長 | MVP: CLI queue |
| Anonymous speaker labels / 匿名說話者標籤 | MVP |
| Local speaker identity / 本機說話者身分 | Planned, sensitive optional layer |

## 隱私模型 / Privacy Model

Audio stays local by default. Dictation logs, meeting transcripts, review queues, personal glossaries, and speaker profiles belong to the user and should live under local paths such as `~/.open-dictate/`.

音檔預設留在本機。語音輸入日誌、會議逐字稿、審核佇列、個人詞庫、說話者資料都屬於使用者，應放在 `~/.open-dictate/` 這類本機路徑。

Speaker embeddings and voiceprints are biometric data. Open Dictate's public repo only ships interfaces, schemas, and fictional examples. Real speaker profiles must stay local and must not be committed.

聲紋與說話者 embedding 是生物特徵資料。Open Dictate 的公開 repo 只提供介面、schema 與虛構範例；真實說話者資料必須留在本機，不能 commit。

Read: [`docs/PRIVACY.md`](docs/PRIVACY.md)

## 快速開始 / Quick Start

```bash
git clone https://github.com/frank890417/open-dictate.git
cd open-dictate
./install.sh          # standalone install: creates .venv-dictate and uses vendor starter glossaries
open /Applications/OpenDictate.app
```

First-time permissions / 第一次使用需要開權限：

1. System Settings → Privacy & Security → enable OpenDictate for:
   - Accessibility
   - Input Monitoring
   - Microphone
2. If you use the `fn` hotkey: Keyboard → “Press 🌐 key to” → Do Nothing.
3. Click any text field, hold `fn`, speak, release.

Full setup: [`docs/SETUP.md`](docs/SETUP.md)

## 即時語音輸入 / Real-time Dictation

- Push-to-talk: `fn` or right Option.
- 16 kHz mono PCM16 recording → Python daemon → corrected text insertion.
- Short-press and silence gates reduce accidental hallucinations.
- Text insertion uses Accessibility direct insertion when possible, then paste fallback.
- Optional microphone selection.

## 會議模式 / Meeting Mode

Meeting Mode creates a reviewable transcript package from longer conversations. It now accepts local audio files through an MLX Whisper adapter, or pre-transcribed JSON/JSONL segments for integrations and tests. The public-safe default still uses anonymous speaker labels; real speaker identity / diarization is not silently guessed and remains a planned optional local layer.

會議模式把長對話整理成可審核的逐字稿包。現在可直接吃本機音檔，透過 MLX Whisper adapter 轉成分段逐字稿；也仍支援 JSON/JSONL 分段輸入，方便整合與測試。公開安全預設仍是匿名說話者標籤；真實說話者身分 / diarization 不會偷偷猜，仍是之後的本機可選層。

```bash
python3 daemon/meeting_cli.py export-demo --out /tmp/open-dictate-demo
python3 daemon/meeting_cli.py transcribe examples/meeting-segments.example.json --out /tmp/open-dictate-meeting
python3 daemon/meeting_cli.py transcribe ~/Desktop/meeting.m4a --out /tmp/open-dictate-meeting-audio --language zh
# Mixed English/Chinese recordings can use: --language auto
```

Read: [`docs/MEETING.md`](docs/MEETING.md)

## 自我進化詞庫 / Self-evolving Glossary

Open Dictate can scan transcripts for possible mishearings and put candidates into a review queue. Accepted candidates update the user's local glossary; rejected candidates stay out.

Open Dictate 可以掃描逐字稿裡的疑似誤聽，把候選放進審核佇列。使用者接受後才寫入本機詞庫；拒絕的候選不會進入確定替換表。

```bash
python3 daemon/qa/mishear_detector.py examples/meeting-segments.example.json --json
python3 daemon/glossary/cli.py add "阿布西店" "Obsidian" --reason "near canonical term"
python3 daemon/glossary/cli.py candidates
```

Rule: **prefer missed corrections over wrong corrections.**

規則：**寧可漏改，不可錯改。**

Read: [`docs/SELF-EVOLVING-GLOSSARY.md`](docs/SELF-EVOLVING-GLOSSARY.md)

## 說話者辨識 / Speaker Identity

The public-safe default is anonymous speaker labels:

公開安全預設是匿名說話者標籤：

```text
SPEAKER_00
SPEAKER_01
```

Optional local speaker identity is planned. It must store profiles under local user-owned paths and treat voice embeddings as sensitive biometric data.

可選的本機說話者身分功能在路線圖中。它必須把資料存在使用者本機路徑，並把聲音 embedding 當成敏感生物特徵資料處理。

Read: [`docs/SPEAKER-ID.md`](docs/SPEAKER-ID.md)

## 架構 / Architecture

```text
[Hotkey]
  → OpenDictate.app
  → /tmp/open-dictate.sock
  → dictated.py (MLX keep-warm)
  → deterministic lexicon correction
  → text insertion
  → ~/.open-dictate/dictation-log/

[Meeting audio or JSON/JSONL]
  → meeting_cli.py
  → local MLX Whisper audio ASR if needed
  → deterministic correction
  → anonymous speaker labels
  → QA flags
  → Markdown / JSONL / SRT / VTT
```

| Component | Path |
|---|---|
| Swift shell | `OpenDictate/` |
| Dictation daemon | `daemon/dictated.py` |
| Meeting CLI | `daemon/meeting_cli.py` |
| QA scanner | `daemon/qa/mishear_detector.py` |
| Review queue | `daemon/glossary/` |
| Speaker layer | `daemon/speaker/` |
| Starter glossaries | `vendor/tools/td-subtitle/glossaries/` |
| Lexicon engine | `vendor/tools/muse-lexicon/muse_lexicon.py` |
| Dictation log | `~/.open-dictate/dictation-log/` |

Architecture and protocol: [`IO-CONTRACT.md`](IO-CONTRACT.md)

## 設計原則 / Design Rules

1. Deterministic replacement only: glossary pairs may correct words; the system must not rewrite the sentence.
2. Prefer missed corrections over wrong corrections.
3. Traditional Chinese normalization is allowed; numeric meaning is not changed automatically.
4. Audio stays local.
5. UI must not steal focus from the current text field.
6. Speaker profiles and voice embeddings are local-only sensitive data.
7. Self-evolution is review-first, not silent self-mutation.

## 建置與測試 / Build and Test

```bash
./build.sh
python3 -m unittest discover tests
python3 scripts/golden-bench.py --skip-daemon
python3 scripts/public-safety-scan.py
./scripts/smoke-test.sh
```

The smoke test builds the app, runs deterministic tests, exports a meeting demo, checks helper scripts, and verifies the signed app bundle.

## 路線圖 / Roadmap

- [x] Public-safe dictation seed.
- [x] Bilingual README and privacy model.
- [x] Meeting package MVP for JSON/JSONL segments.
- [x] QA flags for possible mishearings and numbers.
- [x] Review-first glossary queue CLI.
- [x] Anonymous speaker label layer.
- [x] Local audio ASR backend for Meeting Mode.
- [ ] Optional local speaker profile enrollment.
- [ ] Menubar UI for reviewing glossary candidates.
- [ ] Import/export for user-owned glossary packages.

## Status

Open Dictate is an early public seed extracted from a private daily-use tool, then rebuilt as a public-safe no-history repository. The core dictation path works on Apple Silicon macOS. Meeting, QA, and speaker layers are intentionally conservative MVPs.

Open Dictate 目前是早期 public seed，從私人日用工具抽出後重新整理成公開安全、無歷史包袱的 repo。核心語音輸入路徑已可在 Apple Silicon macOS 上使用；會議、QA、說話者層目前是保守 MVP。

Issues and pull requests are welcome.

## License

MIT. See [`LICENSE`](LICENSE).
