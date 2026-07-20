# Open Dictate Daemon

`dictated.py` is a local keep-warm MLX Whisper daemon. It receives newline-delimited JSON over `/tmp/open-dictate.sock`, transcribes a WAV file, applies deterministic glossary correction, writes a local JSONL log, and returns corrected text.

## Run manually

```bash
cd open-dictate
python3 -m venv .venv-dictate
.venv-dictate/bin/pip install -r daemon/requirements.txt
OPEN_DICTATE_LEXICON_ROOT="$PWD/vendor" .venv-dictate/bin/python daemon/dictated.py
```

## CLI

```bash
python3 daemon/dictate_cli.py ping
python3 daemon/dictate_cli.py stats
python3 daemon/dictate_cli.py file /path/to/16k-mono.wav
```

Production install should use `install.sh`, which generates launchd plists dynamically.
