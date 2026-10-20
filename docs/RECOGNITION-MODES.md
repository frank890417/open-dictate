# Recognition modes: fast and accurate / 辨識模式：快與準

Since daemon 0.7.0 Open Dictate can keep two recognition models loaded and switch between them without a restart. This page says what the two modes are, which one you get by default, how to turn the feature on, how to switch, what gets downloaded, and how fast it is.

daemon 0.7.0 起，Open Dictate 可以同時常駐兩個辨識模型，不用重啟就能切換。這份文件說明兩種模式是什麼、預設是哪一種、怎麼開啟與切換、會下載什麼、實測有多快。

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

Out of the box, **neither**: `./install.sh` sets up the 0.6.x behaviour, one Whisper model (`whisper-large-v3-turbo`) inside the daemon, and the menu-bar app hides the “辨識模式” menu because the daemon reports no modes. Recognition modes are **opt-in**. Once you turn them on (next section), the default mode is **accurate**, and the daemon remembers the last mode you chose in `~/.open-dictate/dictation-mode.json`.

裝好之後**預設兩者都不是**：`./install.sh` 做出來的是 0.6.x 的行為，daemon 裡一個 Whisper 模型（`whisper-large-v3-turbo`），選單列 app 因為 daemon 沒回報模式而隱藏「辨識模式」選單。辨識模式要**自己開**。開了之後預設是**準**，daemon 會把你最後一次的選擇記在 `~/.open-dictate/dictation-mode.json`。

## Turn it on / 開啟

Needs the clone from `./install.sh`, about 5 GB of free memory for the two models (the author's worker, Qwen3-ASR plus large-v3, sits at 5.3 GB resident) and about 4 GB of disk for the downloads (7 GB with large-v3).

需要 `./install.sh` 裝好的 clone、約 5 GB 可用記憶體放兩個模型（作者的 worker 載入 Qwen3-ASR 加 large-v3 常駐 5.3 GB），下載約 4 GB 磁碟（用 large-v3 則 7 GB）。

```bash
cd open-dictate

# 1. a second Python environment for the recognition worker
python3 -m venv .venv-asr
.venv-asr/bin/pip install -r daemon/requirements-asr.txt

# 2. download the models now instead of during the first dictation (Qwen3-ASR 2.5 GB, Whisper turbo 1.6 GB)
.venv-asr/bin/python3 -c "from huggingface_hub import snapshot_download as d; \
d('mlx-community/Qwen3-ASR-1.7B-8bit'); d('mlx-community/whisper-large-v3-turbo')"

# 3. tell the daemon to use the router backend (edit its LaunchAgent, then restart it)
PLIST=~/Library/LaunchAgents/org.opendictate.daemon.plist
PB=/usr/libexec/PlistBuddy
$PB -c "Add :EnvironmentVariables:OPEN_DICTATE_ASR_BACKEND string router" "$PLIST"
$PB -c "Add :EnvironmentVariables:OPEN_DICTATE_ASR_WORKER_PYTHON string $PWD/.venv-asr/bin/python3" "$PLIST"
launchctl kickstart -k gui/$(id -u)/org.opendictate.daemon

# 4. check
python3 daemon/dictate_cli.py mode        # → mode=accurate main=whisper-turbo shadow=None
```

Then the menu-bar icon shows **辨識模式 → 快 / 準**. The daemon takes a moment to start because it loads both models.

⚠️ `./install.sh` rewrites the LaunchAgent, so run step 3 again after every reinstall. To start in fast mode add `OPEN_DICTATE_ASR_MODE` = `fast` the same way (`PlistBuddy -c "Set …"` if the key already exists); it only decides the very first start, after that the mode you last switched to wins.

⚠️ 每次重跑 `./install.sh` 都會重寫 LaunchAgent，第 3 步要再做一次。想一開始就是快：用同樣方式加 `OPEN_DICTATE_ASR_MODE` = `fast`（鍵已存在就用 `PlistBuddy -c "Set …"`）。它只決定第一次啟動，之後以你最後切換的模式為準。

Use `whisper-large-v3` as the accurate model by adding `OPEN_DICTATE_MODEL` = `mlx-community/whisper-large-v3-mlx` (3.1 GB). Other keys: `OPEN_DICTATE_ASR_FAST_MODEL`, `OPEN_DICTATE_ASR_ACCURATE_MODEL`; known model names are `qwen3-asr-1.7b`, `qwen3-asr-0.6b`, `whisper-turbo`, `whisper-v3` (any other value is taken as a Hugging Face repo id). To go back to the default, remove the two keys and restart the daemon.

## Switch / 切換

No restart, and the sentence you are speaking is never affected: each utterance uses the mode it started in.

```bash
python3 daemon/dictate_cli.py mode          # show the current mode
python3 daemon/dictate_cli.py mode 快       # or: fast
python3 daemon/dictate_cli.py mode 準       # or: accurate
```

Or use the menu bar: **辨識模式 → 快 / 準**. Programs can send `{"cmd": "set_mode", "mode": "fast"}` on the socket (see [IO-CONTRACT.md](../IO-CONTRACT.md)). On a default install `mode` prints `mode=None` and a switch request fails with `bad_request`.

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

> **Read this before quoting the numbers.** The Mac was shared with other jobs during every run (load average 9 to 35 on 16 cores), and that alone moved a model's time by up to 2×. So each cell gives the **best time seen** (the least disturbed run) and the **typical time** (median of the runs). The models were visited in turn inside every repetition, so a busy moment slows them all alike and the comparison between rows holds better than either column. A synthetic voice is cleaner than a person in a room: speed carries over, accuracy figures are a sanity check only.

Model time per sentence clip, milliseconds (`asr_engine`, the code the worker and the remote server share; 4 runs, 5 to 9 repetitions each):

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
