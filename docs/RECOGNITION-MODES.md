# Recognition modes: fast and accurate / 辨識模式：快與準

Since daemon 0.7.0 Open Dictate can keep two recognition models loaded and switch between them without a restart. This page says what the two modes are, which one you get by default, how to pick another or turn the feature off, how to switch, what gets downloaded, and how fast it is.

daemon 0.7.0 起，Open Dictate 可以同時常駐兩個辨識模型，不用重啟就能切換。這份文件說明兩種模式是什麼、預設是哪一種、怎麼改選或關掉、怎麼切換、會下載什麼、實測有多快。

## The two modes / 兩種模式

| | Fast / 快 | Accurate / 準 |
|---|---|---|
| Model | Qwen3-ASR 1.7B, 8-bit MLX (`qwen3-asr-1.7b`) | Whisper: `whisper-large-v3-turbo` by default, `whisper-large-v3` if you set it |
| Your glossary reaches the model as | hot words | Whisper's initial prompt |
| Punctuation | the model writes its own; Open Dictate only makes it full-width (`asr_zh`). No LLM in the path | rules (`smart_zh`), or the optional local LLM (`llm_zh`) if you chose it |
| Pick it when | you dictate short messages and notes and what you notice is the wait | you dictate long passages, URLs, version numbers, or you want the path Open Dictate used through 0.6.x |

| | 快 | 準 |
|---|---|---|
| 模型 | Qwen3-ASR 1.7B，8-bit MLX（`qwen3-asr-1.7b`） | Whisper：預設 `whisper-large-v3-turbo`，可改成 `whisper-large-v3` |
| 詞庫怎麼交給模型 | 熱詞 | Whisper 的 initial prompt |
| 標點 | 模型自帶標點，Open Dictate 只轉全形（`asr_zh`），路徑上沒有 LLM | 規則層（`smart_zh`），或你選了的本機 LLM（`llm_zh`） |
| 適合 | 短訊息、短筆記，你在意的是等待時間 | 長段落、網址、版本號，或想走 0.6.x 以來一直在用的那條路 |

What we measured about the difference (details below): on a read-aloud calibration set the two give near-identical text after the Simplified-to-Traditional step, with one visible habit. Qwen3-ASR writes numbers the way you say them (“三千”, “十六比九”, “example 點 com”), Whisper writes digits and URLs (“3000”, “16比9”, “example.com”). Both slips are what the glossary is for, and numbers are never changed automatically.

我們量到的差異（細節在下面）：用朗讀校準稿測，兩者過了簡轉繁之後文字幾乎相同，只有一個看得見的習慣：Qwen3-ASR 照你說的方式寫數字（三千、十六比九、「example 點 com」），Whisper 寫成數字與網址（3000、16比9、example.com）。這類差異正是詞庫要處理的，數字不會被自動改。

## Which one do I get? / 預設是哪一種

**Fast.** Since 0.7.1 a plain `./install.sh` sets up both modes and starts in **fast** (Qwen3-ASR 1.7B). The accurate mode is still there, with the same default model as before (`whisper-large-v3-turbo`), one click or one command away. The daemon remembers the last mode you chose in `~/.open-dictate/dictation-mode.json`.

**快。** 從 0.7.1 起，直接跑 `./install.sh` 會把兩種模式都裝好，並從**快**（Qwen3-ASR 1.7B）開始。準還在，預設模型和以前一樣（`whisper-large-v3-turbo`），選單列點一下或下一行指令就換過去。daemon 會把你最後一次的選擇記在 `~/.open-dictate/dictation-mode.json`。

```bash
./install.sh                          # fast: both modes installed, starts in fast
./install.sh --recognition accurate   # both modes installed, starts in accurate
./install.sh --recognition off        # one Whisper model, as in 0.6.x (no mode menu, 1.6 GB)
./install.sh --plan                   # say what would happen and what would be downloaded; change nothing
```

- **It asks before it downloads.** Before anything is fetched the installer prints which models are missing, their size (4.1 GB for both, 1.6 GB with `off`) and how long that takes (about 22 minutes at 25 Mbps, 5 at 100, 1 at 500), then waits for a yes. With no terminal to ask in (a script, an agent) it refuses until you add `--yes`. Models already on your Mac are not downloaded again and nothing is asked. If a download is interrupted, run `./install.sh` again: files that already finished are not fetched again.
- **Needs** about 5 GB of free memory for the two models (the installer warns below 16 GB of RAM), about 4.1 GB of disk for the models and about 2.6 GB for the two Python environments. Model licences and sources are below.
- **Re-running the installer** rewrites the LaunchAgent. A plain `./install.sh` keeps the mode you last picked from the menu bar; passing `--recognition fast|accurate` sets it again, and `--recognition off` removes the router settings.
- **Coming from 0.7.0 with the modes turned on by hand?** Re-run `./install.sh`: it writes the same keys (and the worker environment becomes `.venv-asr`, as in the 0.7.0 guide), and your remembered mode stays.
- The daemon on its own (`python3 daemon/dictated.py` with no environment, tests, other products built on it) is unchanged: one Whisper model, no modes, unless `OPEN_DICTATE_ASR_BACKEND=router` is set. The installer is what sets it. If you enable the router yourself and set no mode, the daemon starts in accurate.

- **它會先問再下載。** 抓任何東西之前，安裝程式會列出缺哪些模型、大小（兩個一共 4.1 GB，`off` 是 1.6 GB）、要等多久（25 Mbps 約 22 分鐘、100 Mbps 約 5 分鐘、500 Mbps 約 1 分鐘），然後等你回答 y。沒有終端機可以問的時候（腳本、AI 代理）會拒絕，要加 `--yes`。模型已經在你的 Mac 上就不會重抓、也不會問。下載中斷了，重跑 `./install.sh` 就好，已經下載完成的檔案不會重抓。
- **需要**約 5 GB 可用記憶體放兩個模型（RAM 不到 16 GB 時安裝程式會提醒）、模型約 4.1 GB 磁碟、兩個 Python 環境約 2.6 GB 磁碟。模型授權與來源在下面。
- **重跑安裝程式**會重寫 LaunchAgent。單純 `./install.sh` 會保留你在選單列最後選的模式。加 `--recognition fast|accurate` 會重設，`--recognition off` 會拿掉 router 設定。
- **從 0.7.0 手動開啟的人**：重跑 `./install.sh` 就好，它寫的是同一組鍵（worker 環境同樣是 `.venv-asr`），你記住的模式不變。
- daemon 本身（不經安裝程式直接跑 `python3 daemon/dictated.py`、測試、別的產品拿它當核心）沒有改：除非設了 `OPEN_DICTATE_ASR_BACKEND=router`，否則還是單一 Whisper 模型、沒有模式。設這個的是安裝程式。你自己開 router 又沒指定模式時，daemon 從準開始。

## What the installer sets up / 安裝程式做了什麼

Nothing hidden: this is what `./install.sh` does for the modes, and what you would type by hand.

```bash
cd open-dictate

# 1. a second Python environment for the recognition worker
python3 -m venv .venv-asr
.venv-asr/bin/pip install -r daemon/requirements-asr.txt

# 2. the models (the installer asks first and shows the size; here Qwen3-ASR 2.5 GB, Whisper turbo 1.6 GB)
.venv-asr/bin/python3 scripts/install-recognition.py download --mode fast

# 3. the daemon's LaunchAgent gets the router settings (then it is restarted)
PLIST=~/Library/LaunchAgents/org.opendictate.daemon.plist
PB=/usr/libexec/PlistBuddy
$PB -c "Add :EnvironmentVariables:OPEN_DICTATE_ASR_BACKEND string router" "$PLIST"
$PB -c "Add :EnvironmentVariables:OPEN_DICTATE_ASR_WORKER_PYTHON string $PWD/.venv-asr/bin/python3" "$PLIST"
$PB -c "Add :EnvironmentVariables:OPEN_DICTATE_ASR_MODE string fast" "$PLIST"
launchctl kickstart -k gui/$(id -u)/org.opendictate.daemon

# 4. check
python3 daemon/dictate_cli.py mode        # → mode=fast main=qwen3-asr-1.7b shadow=None
```

The `OPEN_DICTATE_ASR_MODE` key only decides the very first start; after that the mode you last switched to wins. Use `whisper-large-v3` as the accurate model by also adding `OPEN_DICTATE_MODEL` = `mlx-community/whisper-large-v3-mlx` (3.1 GB, not downloaded by the installer; re-running the installer rewrites the plist, so add it again). Other keys: `OPEN_DICTATE_ASR_FAST_MODEL`, `OPEN_DICTATE_ASR_ACCURATE_MODEL`; known model names are `qwen3-asr-1.7b`, `qwen3-asr-0.6b`, `whisper-turbo`, `whisper-v3` (any other value is taken as a Hugging Face repo id).

這一段是 `./install.sh` 為模式做的事，也是你手動會打的指令：建第二個 Python 環境、下載模型、在 daemon 的 LaunchAgent 加 router 設定再重啟、最後檢查。`OPEN_DICTATE_ASR_MODE` 只決定第一次啟動，之後以你最後切換的模式為準。想用 `whisper-large-v3` 當準的模型，另外加 `OPEN_DICTATE_MODEL` = `mlx-community/whisper-large-v3-mlx`（3.1 GB，安裝程式不會下載它，重跑安裝程式會重寫 plist，要再加一次）。

## The punctuation model in Ollama / Ollama 裡的標點模型

LLM punctuation (`llm_zh`, an option in the menu bar) asks a local Ollama for a model (by default `qwen3.6:35b-a3b-coding-nvfp4`, 21 GB). **Since 0.7.2 the daemon loads it only when it is needed.** In fast mode it never does: Qwen3-ASR writes its own punctuation and no LLM is in the path. In accurate mode it does nothing until the first sentence that goes to the LLM; that sentence is punctuated by the rules while the model loads in the background, and the model then stays warm. Switching to fast lets go: the daemon stops reloading it, and Ollama unloads it when its `keep_alive` runs out (24 h by default; the daemon never unloads a model itself, because other programs may be using it).

LLM 標點（`llm_zh`，選單列的選項）會向本機的 Ollama 要一個模型（預設 `qwen3.6:35b-a3b-coding-nvfp4`，21 GB）。**從 0.7.2 起，daemon 只在需要的時候才載入它。**快速模式永遠不會：Qwen3-ASR 自己寫標點，路徑上沒有 LLM。準確模式在第一句要走 LLM 的句子出現之前什麼都不做，那一句先用規則層標點，模型在背景載入，之後保持暖機。切到快速模式就鬆手：daemon 不再重新載入，Ollama 在 `keep_alive` 到期時（預設 24 小時）自己卸載。daemon 不會主動卸載模型，因為別的程式可能也在用。

To change that, set `OPEN_DICTATE_PUNCT_WARM` in the daemon's LaunchAgent (`PlistBuddy -c "Add :EnvironmentVariables:OPEN_DICTATE_PUNCT_WARM string 1" …`, then restart the daemon; re-running `./install.sh` rewrites that file):

| `OPEN_DICTATE_PUNCT_WARM` | what happens |
|---|---|
| not set, or `auto` | default: warm only when an LLM-punctuation sentence needs the model |
| `1` | load at start and keep it resident, whatever the mode (the 0.6.0 to 0.7.1 behaviour) |
| `0` | never warm; LLM punctuation then asks Ollama directly and may time out while the model loads |

要改的話，在 daemon 的 LaunchAgent 設 `OPEN_DICTATE_PUNCT_WARM`（用法如上，設完重啟 daemon，重跑 `./install.sh` 會重寫那個檔案）。沒設或 `auto`：預設，需要才暖。`1`：開機就載入並常駐，不管什麼模式（0.6.0 到 0.7.1 的行為）。`0`：不暖，LLM 標點會直接問 Ollama，模型載入期間可能逾時。

## Switch / 切換

No restart, and the sentence you are speaking is never affected: each utterance uses the mode it started in.

```bash
python3 daemon/dictate_cli.py mode          # show the current mode
python3 daemon/dictate_cli.py mode 快       # or: fast
python3 daemon/dictate_cli.py mode 準       # or: accurate
```

Or use the menu bar: **辨識模式 → 快 / 準**. Programs can send `{"cmd": "set_mode", "mode": "fast"}` on the socket (see [IO-CONTRACT.md](../IO-CONTRACT.md)). With `--recognition off` (or any daemon without the router) `mode` prints `mode=None` and a switch request fails with `bad_request`.

## What gets downloaded, and under which licence / 下載什麼、授權為何

Open Dictate does not ship or redistribute any model file. Each model is downloaded from Hugging Face by the user's own Mac the first time it is needed, and stays in the Hugging Face cache (`~/.cache/huggingface`).

Open Dictate 不內附、也不散布任何模型檔。每個模型在第一次用到時，由你自己的 Mac 從 Hugging Face 下載，存在 Hugging Face 的快取目錄。

| Model | Downloaded repo | Size | Licence | Source |
|---|---|---|---|---|
| Qwen3-ASR 1.7B (fast) | `mlx-community/Qwen3-ASR-1.7B-8bit` | 2.5 GB | Apache-2.0 | [model card](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) · [licence](https://github.com/QwenLM/Qwen3-ASR/blob/main/LICENSE) |
| Whisper large-v3-turbo (accurate, default) | `mlx-community/whisper-large-v3-turbo` | 1.6 GB | MIT | [openai/whisper](https://github.com/openai/whisper/blob/main/LICENSE) · [model card](https://huggingface.co/openai/whisper-large-v3-turbo) |
| Whisper large-v3 (accurate, optional) | `mlx-community/whisper-large-v3-mlx` | 3.1 GB | MIT (MLX conversion); Apache-2.0 on OpenAI's own model card | [mlx card](https://huggingface.co/mlx-community/whisper-large-v3-mlx) · [openai card](https://huggingface.co/openai/whisper-large-v3) |

Python packages (`daemon/requirements-asr.txt`, installed from PyPI, not bundled): [mlx-audio](https://github.com/Blaizzy/mlx-audio/blob/main/LICENSE) 0.5.7 (MIT), [mlx-whisper](https://github.com/ml-explore/mlx-examples/blob/main/LICENSE) 0.4.3 (MIT), soundfile 0.14.0 (BSD-3-Clause). All licences above allow personal and commercial use with attribution; check them again if you redistribute the models yourself. Last checked 2026-10-20.

## Privacy / 隱私

Both modes run on your Mac. The recognition worker is a local child process of the daemon; audio does not leave the machine.

兩種模式都在你的 Mac 上跑。辨識 worker 是 daemon 的本機子行程，音訊不會離開這台機器。

**One option changes that.** If you also set `OPEN_DICTATE_ASR_REMOTE_URL` (and `OPEN_DICTATE_ASR_REMOTE_KEYCHAIN`, the keychain service that holds the shared token), each clip is first sent as FLAC to `daemon/asr_server.py` on the machine you named, and the local worker is only the fallback. Use it only with a computer you own, on a private network; the server binds one address, refuses `0.0.0.0`, compares the token in constant time and writes timings only, never audio or text. Leave both variables unset and nothing is sent anywhere. In the log, `asr_where` says `local` or `studio`; `studio` is simply the name this field uses for “the remote recognition service”.

**有一個選項會改變這件事。** 如果你另外設了 `OPEN_DICTATE_ASR_REMOTE_URL`（與放共用 token 的鑰匙圈 service `OPEN_DICTATE_ASR_REMOTE_KEYCHAIN`），每段錄音會先以 FLAC 送到你指定那台機器上的 `daemon/asr_server.py`，本機 worker 只當備援。只對你自己擁有、在私人網路裡的電腦使用。伺服器只綁一個位址、拒絕 `0.0.0.0`、用定時比較檢查 token、只記時間不記音訊與文字。兩個變數都不設，就什麼都不會送出。紀錄裡的 `asr_where` 是 `local` 或 `studio`，`studio` 只是這個欄位對「遠端辨識服務」的稱呼。

## How fast / 實測速度

Measured with `scripts/modes-bench.py` on 2026-10-20: MacBook Pro, Apple M4 Max, 128 GB, macOS 27.0.1, Python 3.14.7, models already on disk. The audio is the five fictional sentences of `fixtures/calibration-v1.json` read by the macOS voice Meijia (6.6 to 14.1 s each, 50.3 s joined), so anyone can repeat it.

> **Read this before quoting the numbers.** The Mac was shared with other jobs during every run (load average 9 to 35 on 16 cores), and that alone moved a model's time by up to 2×. So each cell gives the **best time seen** (the least disturbed run) and the **typical time** (median of the runs). In the last two model-time runs and in every whole-request run the models (or modes) were visited in turn inside each repetition, so a busy moment slows them all alike and the comparison between rows holds better than either column. A synthetic voice is cleaner than a person in a room: speed carries over, accuracy figures are a sanity check only.

Model time per sentence clip, milliseconds (`asr_engine`, the code the worker and the remote server share; 4 runs of 5 to 9 repetitions, the first two timing the models one after another, the last two interleaving them):

| Model | best seen | typical | 50 s clip, best / typical |
|---|---|---|---|
| Qwen3-ASR 1.7B 8-bit (fast) | 189 – 294 | 301 – 407 | 1121 / 2090 |
| Whisper large-v3-turbo | 342 – 401 | 465 – 734 | 953 / 2541 |
| Whisper large-v3 | 568 – 739 | 1310 – 1807 | 2663 / 6244 |

A whole request through a throw-away daemon (router backend, the glossary prompt or hot words, full-width punctuation; 7 repetitions per clip), milliseconds per sentence clip:

| Accurate mode is… | Fast mode, best / typical | Accurate mode, best / typical |
|---|---|---|
| Whisper large-v3-turbo (the default) | 376 – 630 / 600 – 828 | 350 – 711 / 570 – 963 |
| Whisper large-v3 | 516 – 799 / 591 – 1051 | 1699 – 2411 / 1907 – 3232 |

What that says, plainly: **fast is about three times quicker than Whisper large-v3.** Against large-v3-turbo, the default accurate model, it is about 1.5× quicker in model time and roughly level over a whole request, so there the choice is about the model and the punctuation path, not about speed. The speed gain is real if you want large-v3 as your accurate mode. The model start-up is 3 to 18 s with the weights on disk; the first start downloads them.

Accuracy sanity check: character error rate on the five sentences after the daemon's Simplified-to-Traditional step, mean over the sentences: Qwen3-ASR 0.031, Whisper large-v3 0.032, Whisper large-v3-turbo 0.038 (every model reads the number-and-URL sentence worst, for the reason in the table at the top). The two modes make different small mistakes, for example Qwen3-ASR heard 寧可 as 您可 in one run. That is too little data to rank them on real speech, and we do not claim a ranking.

Repeat it:

```bash
.venv-asr/bin/pip install opencc-python-reimplemented     # optional: Traditional-script scoring
.venv-asr/bin/python3 scripts/modes-bench.py                # model time, three models, interleaved
.venv-asr/bin/python3 scripts/modes-bench.py --e2e --daemon-python .venv-dictate/bin/python3
```

`--e2e` starts its own daemon on a private socket in a temporary folder; it does not touch an installed daemon, its socket or `~/.open-dictate`.
