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

The first run also downloads and warms the Whisper model. This can take about two minutes depending on the network and disk cache. The installer displays progress for up to 180 seconds and only succeeds after a real daemon ping reports ready; a socket file alone is not considered success.

第一次執行也會下載並載入 Whisper 模型，依網路與磁碟快取狀況可能約需兩分鐘。安裝器最多顯示 180 秒進度，並以實際 daemon ping 為成功條件；只有 socket 檔案不算完成。

If a slow connection needs more time, set `OPEN_DICTATE_WARM_TIMEOUT=300 ./install.sh`.

## Permissions

Enable OpenDictate in macOS Privacy & Security:

- Microphone
- Accessibility
- Input Monitoring

If you rebuild the app with ad-hoc signing, macOS may require toggling permissions off/on.

### Hotkey conflicts

Open Dictate defaults to `fn` push-to-talk. In System Settings → Keyboard:

- Set “Press fn/🌐 key to” to “Do Nothing”.
- Disable or move Apple Dictation's shortcut if it also uses `fn`.
- Quit or reconfigure other dictation apps that capture `fn`.

If the hotkey still does not work, re-check Input Monitoring and Accessibility, then quit and reopen OpenDictate.

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

## Diagnose

Run the read-only doctor after moving the clone, after a macOS update, or when the menu-bar app cannot reach the daemon:

```bash
./scripts/doctor.sh
```

It checks the installed app, code signature, plist syntax, launchd jobs, clone paths, Unix socket, and a real ping. It only prints reminders for Microphone, Accessibility, and Input Monitoring because it does not inspect or modify the protected macOS TCC database.

## Uninstall

```bash
./uninstall.sh
```

This removes `/Applications/OpenDictate.app` and both LaunchAgents, while preserving `~/.open-dictate` (personal glossaries, transcripts, and logs).

```bash
./uninstall.sh --purge-data
```

For safety, `--purge-data` only prints the local data path and asks you to move it to Trash manually. The script never destroys user data automatically.
