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

## Remote recognition (daemon ≥0.7.0)

`ASR_BACKEND=router` moves recognition into `asr_worker.py` and, optionally, to
`asr_server.py` on another Mac (see IO-CONTRACT §Daemon 0.7.0). Worker/server environment:

```bash
python3 -m venv .venv-asr && .venv-asr/bin/pip install -r daemon/requirements-asr.txt
# on the other Mac (token from its keychain, service name of your choice, account "token"):
.venv-asr/bin/python daemon/asr_server.py --bind <tailnet-ip> --port 8795 \
    --models whisper-v3,qwen3-asr-1.7b --token-keychain my-asr-token
```
