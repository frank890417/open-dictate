# Open Dictate Setup

## Requirements

- Apple Silicon Mac.
- macOS 14 or later.
- Xcode command line tools.
- Python 3.11+ recommended.
- About 5 GB of free memory for the two recognition models, and about 7 GB of disk (4.1 GB of models, 2.6 GB of Python environments). With `--recognition off`: 1.6 GB of models.

## Install

```bash
git clone https://github.com/frank890417/open-dictate.git
cd open-dictate
./install.sh
```

The installer first tells you which models it has to download, how big they are and how long that takes, and waits for a yes. Then it creates `.venv-dictate` and `.venv-asr`, installs Python dependencies, downloads the models, builds `OpenDictate.app`, installs it to `/Applications/OpenDictate.app`, generates launchd plists with your current clone path, and starts the daemon/app. It succeeds only after a real daemon ping reports ready and the recognition mode answers; a socket file alone is not considered success.

安裝程式會先說明要下載哪些模型、多大、要等多久，等你同意，再建立 `.venv-dictate` 與 `.venv-asr`、安裝 Python 依賴、下載模型、build `OpenDictate.app` 裝到 `/Applications/OpenDictate.app`、用你目前的 clone 路徑產生 launchd plist，最後啟動 daemon 與 app。要等 daemon 真的回應 ping、辨識模式也有回應才算成功，只有 socket 檔案不算完成。

```bash
./install.sh --plan                   # what it would install and download; changes nothing
./install.sh                          # fast + accurate modes, starts in fast (4.1 GB of models)
./install.sh --recognition accurate   # same, starts in accurate
./install.sh --recognition off        # one Whisper model (1.6 GB), no mode menu, as in 0.6.x
./install.sh --yes                    # accept the download without being asked (scripts, AI agents)
```

Download time depends on your connection (about 22 minutes for 4.1 GB at 25 Mbps, 5 at 100 Mbps, 1 at 500 Mbps). If a download is interrupted, run the installer again: files that already finished are not fetched again. Add `--yes` when no terminal is there to ask; without it the installer stops before changing anything. Models are cached by Hugging Face under `~/.cache/huggingface`, so a reinstall does not download them again.

下載要多久看你的網路（4.1 GB 在 25 Mbps 約 22 分鐘、100 Mbps 約 5 分鐘、500 Mbps 約 1 分鐘）。下載中斷後再跑安裝程式就好，已經下載完成的檔案不會重抓。沒有終端機可以問的時候要加 `--yes`，不加的話安裝程式會在改動任何東西之前停下來。模型由 Hugging Face 快取在 `~/.cache/huggingface`，重裝不會再下載。

Two environment variables for unusual setups: `OPEN_DICTATE_APP_DIR` installs the app somewhere other than `/Applications` (no admin rights needed), and `OPEN_DICTATE_SOCKET_PATH` moves the daemon socket; `doctor.sh` and `uninstall.sh` read the same two.

If a slow connection needs more time, set `OPEN_DICTATE_WARM_TIMEOUT=300 ./install.sh`.

## Fast and accurate recognition modes

The installer keeps two models loaded, a fast one (Qwen3-ASR, the starting mode) and an accurate one (Whisper), and you switch between them from the menu bar. What each mode is, the model downloads and their licences, and how to start in accurate or turn the feature off: [RECOGNITION-MODES.md](RECOGNITION-MODES.md). 安裝程式會把快的（Qwen3-ASR，起始模式）和準的（Whisper）兩個模型都常駐，你在選單列切換。每種模式是什麼、模型下載與授權、怎麼從準開始或關掉，見 [RECOGNITION-MODES.md](RECOGNITION-MODES.md)。

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
