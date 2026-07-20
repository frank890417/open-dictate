# Open Dictate Setup

## Requirements

- Apple Silicon Mac.
- macOS 14 or later.
- Xcode command line tools.
- Python 3.11+ recommended.

## Install

```bash
git clone https://github.com/frank890417/open-dictate.git
cd open-dictate
./install.sh
```

The installer creates `.venv-dictate`, installs Python dependencies, builds `OpenDictate.app`, installs it to `/Applications/OpenDictate.app`, generates launchd plists with your current clone path, and starts the daemon/app.

## Permissions

Enable OpenDictate in macOS Privacy & Security:

- Microphone
- Accessibility
- Input Monitoring

If you rebuild the app with ad-hoc signing, macOS may require toggling permissions off/on.

## Optional External Glossary Root

By default Open Dictate uses `vendor/tools/...` starter glossaries. Advanced users may set:

```bash
OPEN_DICTATE_LEXICON_ROOT=/path/to/compatible/root ./install.sh --developer
```

The root must contain:

```text
tools/muse-lexicon/muse_lexicon.py
tools/td-subtitle/glossaries/*.json
```

## Manual Commands

```bash
./build.sh
python3 daemon/dictate_cli.py ping
python3 daemon/dictate_cli.py stats
python3 scripts/golden-bench.py --skip-daemon
```
