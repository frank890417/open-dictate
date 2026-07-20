# 會議模式 / Meeting Mode

Meeting Mode turns longer recordings or pre-transcribed segments into reviewable transcript packages.

會議模式用來把長音檔或已轉好的分段逐字稿整理成可審核的逐字稿包。

## Current Public MVP

The current public MVP processes JSON/JSONL segments. It does not fake audio ASR. Audio ASR will be added as a local backend later.

目前公開版 MVP 先處理 JSON/JSONL 分段逐字稿，不假裝已經完成音檔 ASR。音檔 ASR 會以本地後端方式接上。

```bash
python3 daemon/meeting_cli.py export-demo --out /tmp/open-dictate-demo
python3 daemon/meeting_cli.py transcribe examples/meeting-segments.example.json --out /tmp/open-dictate-meeting
```

Output:

```text
transcript.jsonl
transcript.md
transcript.srt
transcript.vtt
meeting-result.json
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

## Safety Rule

Meeting Mode may flag uncertain spans, but it does not silently rewrite names, numbers, dates, or money-like values.
