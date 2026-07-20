# 自我進化詞庫 / Self-evolving Glossary

Open Dictate's glossary loop is review-first:

```text
transcript
  → QA scanner finds possible mishearings
  → candidate enters review queue
  → user accepts / edits / rejects
  → accepted pair updates local glossary
  → future transcription improves
```

Open Dictate 的詞庫成長採審核優先：系統可以找候選，但不應把猜測直接寫進確定替換表。

## Commands

```bash
python3 daemon/qa/mishear_detector.py examples/meeting-segments.example.json --json
python3 daemon/glossary/cli.py add "阿布西店" "Obsidian" --reason "near canonical term"
python3 daemon/glossary/cli.py candidates
python3 daemon/glossary/cli.py accept cand_xxxxx
python3 daemon/glossary/cli.py reject cand_xxxxx --reason "valid phrase"
python3 daemon/glossary/cli.py undo cand_xxxxx
```

## Buckets

- `replacements`: safe deterministic wrong→right pairs.
- `_contextual`: risky pairs that need context.
- `_review_queue`: pending suggestions.
- `_history`: audit trail.

## Rule

Prefer missed corrections over wrong corrections. A bad glossary pair can poison every future transcript.

寧可漏改，不可錯改。一個錯誤詞庫 pair 會污染之後每一次轉錄。
