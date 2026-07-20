# Contributing to Open Dictate

Thanks for helping improve Open Dictate.

謝謝你一起改善 Open Dictate。

## Ground Rules

- Keep examples fictional or public-domain.
- Do not commit real audio, real transcripts, dictation logs, private paths, personal glossaries, or speaker profiles.
- Do not paste private transcripts or voiceprints into GitHub issues.
- Prefer deterministic correction and review queues over silent rewriting.
- Run the safety and test commands before opening a pull request.

## Before Pull Request

```bash
python3 scripts/public-safety-scan.py
python3 -m unittest discover tests
python3 scripts/golden-bench.py --skip-daemon
```

If you change Swift code, also run:

```bash
./build.sh
```

## Privacy

Read [`docs/PRIVACY.md`](docs/PRIVACY.md). Speaker profiles and voice embeddings are sensitive biometric data. Public fixtures must use fake examples only.
