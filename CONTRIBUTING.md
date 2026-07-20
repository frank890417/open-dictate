# Contributing to Open Dictate

- Do not upload private voice recordings, dictation logs, or personal transcripts in issues or pull requests.
- Use fictional or public-domain text fixtures.
- Keep correction deterministic unless an optional local model is protected by a no-rewrite gate.
- Run `python3 scripts/golden-bench.py --skip-daemon` and `./build.sh` before opening a pull request.
