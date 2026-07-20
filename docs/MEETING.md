# 會議模式 / Meeting Mode

Meeting Mode turns longer recordings or pre-transcribed segments into reviewable transcript packages.

會議模式用來把長音檔或已轉好的分段逐字稿整理成可審核的逐字稿包。

## Current Public Seed

The current public seed accepts two input types:

1. **Local audio files** (`.wav`, `.m4a`, `.mp3`, `.flac`, `.aiff`) through a local MLX Whisper adapter.
2. **Pre-transcribed JSON/JSONL segments** for integrations, fixtures, and testing.

目前公開版支援兩種輸入：

1. **本機音檔**（`.wav`, `.m4a`, `.mp3`, `.flac`, `.aiff`），透過本地 MLX Whisper adapter 轉錄。
2. **已轉好的 JSON/JSONL 分段逐字稿**，給整合、fixture 與測試使用。

```bash
python3 daemon/meeting_cli.py export-demo --out /tmp/open-dictate-demo
python3 daemon/meeting_cli.py transcribe examples/meeting-segments.example.json --out /tmp/open-dictate-meeting
python3 daemon/meeting_cli.py transcribe ~/Desktop/meeting.m4a --out /tmp/open-dictate-meeting-audio --language zh
```

For mixed English/Chinese meetings, use `--language auto`. To override the ASR model, use `--model mlx-community/whisper-large-v3-turbo` or another compatible MLX Whisper repo.

中英混雜會議可用 `--language auto`。要指定 ASR 模型可加 `--model mlx-community/whisper-large-v3-turbo` 或其他相容的 MLX Whisper repo。

Output:

```text
transcript.jsonl
transcript.md
transcript.srt
transcript.vtt
meeting-result.json
```

## Pipeline

```text
Audio file
  → mlx_whisper.transcribe() locally
  → segment contract
  → deterministic glossary correction
  → anonymous speaker labels
  → QA flags
  → Markdown / JSONL / SRT / VTT
```

```text
JSON/JSONL segments
  → segment contract
  → deterministic glossary correction
  → anonymous speaker labels
  → QA flags
  → Markdown / JSONL / SRT / VTT
```

## Segment Input Schema

```json
{
  "segments": [
    {
      "start": 0.0,
      "end": 3.2,
      "speaker": "alice-local-label",
      "raw": "今天我們測試 Open Dictate"
    }
  ]
}
```

Speaker labels are normalized to anonymous labels by default:

```text
SPEAKER_00
SPEAKER_01
```

Audio input currently receives `SPEAKER_00` unless an upstream segment already includes labels. Real speaker identity and cross-meeting voiceprint learning are planned as a separate optional local layer, because voice embeddings are sensitive biometric data.

音檔輸入目前預設標成 `SPEAKER_00`，除非上游分段本身已有 speaker label。真實說話者身分與跨會議聲紋學習會拆成另外一個本機可選層，因為聲紋 embedding 是敏感生物特徵資料。

## Safety Rule

Meeting Mode may flag uncertain spans, but it does not silently rewrite names, numbers, dates, or money-like values.

會議模式可以標記不確定片段，但不會靜默改寫人名、數字、日期或金額類資訊。
