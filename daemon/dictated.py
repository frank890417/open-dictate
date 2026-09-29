#!/usr/bin/env python3
"""dictated — open-dictate dictation daemon v0.1

MLX Whisper keep-warm + unix socket + muse_lexicon 確定性校正。
契約 SSOT：~/Projects/open-dictate/IO-CONTRACT.md（改協議先改契約）。

流程：Swift 殼錄 16k/mono/PCM16 wav → socket 送 {"cmd":"transcribe","wav":...}
     → mlx_whisper（常駐權重）→ lex.correct()（絕無 LLM）→ 回 JSON → 刪 /tmp wav → log jsonl。

跑法（需要有 mlx-whisper 的 Python 環境）：
    python3 dictated.py
launchd 安裝見 daemon/README.md。
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import wave
from datetime import datetime
from pathlib import Path

try:
    from .product_config import (LOG_ROOT, LEXICON_ROOT, PRIORITY_TERMS, SOCKET_PATH, env,
                                 PROMPT_CORE_TERMS as PROMPT_CORE_TERMS_RAW)
except ImportError:  # direct script execution: python daemon/dictated.py
    from product_config import (LOG_ROOT, LEXICON_ROOT, PRIORITY_TERMS, SOCKET_PATH, env,
                                PROMPT_CORE_TERMS as PROMPT_CORE_TERMS_RAW)

__version__ = "0.6.0"

# ---------------------------------------------------------------------------
# 常數（路徑皆契約 SSOT，見 IO-CONTRACT.md §路徑約定）
# ---------------------------------------------------------------------------
# 預設留 turbo（下載小、中位延遲低）。完整版 large-v3 迴圈幻覺明顯更少但檔案大一倍，
# 由各產品自行以 product config 決定，不在核心寫死。
MODEL = env("MODEL", "mlx-community/whisper-large-v3-turbo")
LOG_DIR = LOG_ROOT

SAMPLE_RATE = 16000          # 契約：殼交付 16kHz mono PCM16
MIN_DURATION_S = 0.5         # <0.5s → no_speech（殼理論上不送，daemon 雙保險）
NO_SPEECH_PROB = 0.6         # segment no_speech_prob 門檻
SILENCE_RMS = 2e-4           # 能量閘門：rms 低於 PCM16 量化底(~3e-5)數倍 = 物理上無語音。
                             # 實測 whisper-large-v3-turbo 對全零音訊 no_speech_prob=0.0 且
                             # 自信幻覺（"优优独播剧场"），nsp 對數位靜音完全失效 → ASR 前先擋。
RECV_LIMIT = 1 << 20         # 單請求上限 1MB（實際只是 wav 路徑，防呆）
SOCKET_TIMEOUT_S = 15        # 單連線 recv/send timeout，防 client 掛住 daemon

# ---------------------------------------------------------------------------
# ASR 解碼護欄（v0.6.0）
#
# 失敗形狀（重放實測）：誤觸的 0.8–1.6 秒錄音在底噪上掉進 token 迴圈，
# mlx_whisper 預設的溫度 fallback 一個窗口「重複度過高」就換溫度整窗重解，
# 最多 6 輪、每輪最多 224 token ⇒ 單筆 15–41 秒，超過殼的逾時、字沒進游標。
# 兩道護欄：
#   ① sample_len 隨音長給上限：真實口述 p99 約 7 text token/秒，
#      給 12/秒 + 32 的空間，1.5 秒最多 50 token。≥16 秒就等於原本的 224，長口述不受影響。
#   ② 溫度階梯截在 0.4：重放語料裡用到 1.0 的解碼 15 次有 12 次是垃圾
#      （「44444」「4.4 on on…」），剩下 3 次也只是垃圾被好段落稀釋。
#      救不回來的交給下面的「換 prompt 重試」。短錄音（<4 秒）只留兩個溫度。
# ---------------------------------------------------------------------------
ASR_TOKENS_PER_S = 12.0
ASR_TOKENS_BASE = 32
ASR_SAMPLE_LEN_MAX = 224     # whisper n_text_ctx // 2（原本的預設值）
ASR_SHORT_CLIP_S = 4.0
ASR_TEMPS = (0.0, 0.2, 0.4)
ASR_SHORT_TEMPS = (0.0, 0.4)


def asr_decode_options(dur: float | None) -> dict:
    """依音長給 mlx_whisper.transcribe 的額外參數（sample_len／temperature）。"""
    if not dur or dur <= 0:
        return {}
    import math
    opts: dict = {"sample_len": min(ASR_SAMPLE_LEN_MAX,
                                    int(math.ceil(dur * ASR_TOKENS_PER_S)) + ASR_TOKENS_BASE)}
    opts["temperature"] = ASR_SHORT_TEMPS if dur < ASR_SHORT_CLIP_S else ASR_TEMPS
    return opts


# ---------------------------------------------------------------------------
# 換 prompt 重試（v0.6.0）
#
# 重放語料裡每一種 prompt 都會在「某幾段」崩掉，而且崩的段落不一樣：
# 長 prompt 崩的段落，換成只有詞表的短 prompt 就救回整段；短 prompt 崩的，不給 prompt 又救回來。
# 換溫度救不回來、換 prompt 救得回來——所以異常時依序換 prompt 重解一次。
# 異常＝≥4 秒、有講話的形狀（voiced），但留下的內容字數 < 有聲秒數 × 1（正常口述每秒 4–5 字）。
# 近殼逾時就不重試（寧可 no_speech，也不要殼斷線）。
# ---------------------------------------------------------------------------
ASR_ANOMALY_CHARS_PER_VOICED_S = 1.0
ASR_ANOMALY_MIN_VOICED = 0.25
ASR_RETRY_RTF_BUDGET = 0.15       # 預估重試成本＝音長 × 0.15 秒（實測 T=0 解碼 RTF 0.05–0.1）


def asr_output_anomalous(content: str, dur: float | None, voiced: float | None) -> bool:
    if not dur or dur < ASR_SHORT_CLIP_S or voiced is None or voiced < ASR_ANOMALY_MIN_VOICED:
        return False
    return len(_content_chars(content)) < dur * voiced * ASR_ANOMALY_CHARS_PER_VOICED_S


# 小寫形標點（U+FE50–FE57）→ 一般全形。large-v3 在中文標點 prompt 下會吐「﹐」「﹖」
# （重放：24 段裡 14 段出現），規則層與 LLM 閘門都不認得它們。
_SMALL_FORMS = str.maketrans({"﹐": "，", "﹑": "、", "﹒": "。", "﹔": "；", "﹕": "：",
                              "﹖": "？", "﹗": "！"})


def normalize_small_forms(text: str) -> str:
    return (text or "").translate(_SMALL_FORMS)


# ---------------------------------------------------------------------------
# 誤觸閘門（v0.6.0）：短錄音先看「有沒有講話的形狀」再決定要不要進 ASR
#
# 舊的 SILENCE_RMS 只擋數位靜音（全零）；真麥克風的房間底噪 rms 在 1e-3–3e-3，
# 永遠過得了那道門，進 ASR 就吐署名幻覺。講話的特徵是「有起伏」：
# 以 30ms 幀的 rms 算動態範圍 dr = 20·log10(p90/p10)、有聲比 voiced = 幀 rms > 4×p10 的比例。
# 重放實測（真實錄音切出）：房間底噪 24 段 dr 2.4–14.0 dB、voiced 0–0.17；
# 真人 0.3–1.4 秒短語 30 段 dr 15.1–35.9 dB、voiced 0.23–0.78。
# 門檻 dr<9 且 voiced<0.08：底噪攔下 15/24，真人短語誤殺 0/30。只對 <4 秒錄音生效。
# 守「寧可漏改」：門檻刻意保守，dr／voiced 寫進 log，之後用真實資料再調。
# ---------------------------------------------------------------------------
GATE_MAX_DUR_S = 4.0
GATE_DR_DB = 9.0
GATE_VOICED = 0.08
_GATE_FRAME = 480            # 30ms @16k


def speech_shape(audio) -> tuple[float, float]:
    """回 (dr_db, voiced_ratio)。音太短（<3 幀）回 (99, 1) ＝ 不判。"""
    n = len(audio) // _GATE_FRAME
    if n < 3:
        return 99.0, 1.0
    r = np.sqrt(np.square(audio[: n * _GATE_FRAME].reshape(n, _GATE_FRAME)).mean(axis=1)) + 1e-7
    p10, p90 = np.percentile(r, [10, 90])
    return float(20 * np.log10(p90 / p10)), float((r > 4 * p10).mean())


def looks_like_no_speech(audio, dur: float | None) -> tuple[bool, float, float]:
    dr, voiced = speech_shape(audio)
    hit = bool(dur is not None and dur < GATE_MAX_DUR_S and dr < GATE_DR_DB and voiced < GATE_VOICED)
    return hit, dr, voiced


# ---------------------------------------------------------------------------
# 頭尾靜音裁切（v0.6.0）
#
# 頭尾靜音對轉錄沒有資訊，只會給幻覺空間、多花解碼時間 → 進 ASR 前裁掉。
# 保守條件：整段要有明確的「安靜底」（dr ≥ 15 dB，連續講話沒有底就不裁）、
# 靜音要超過 0.6 秒才動、前留 0.3 秒後留 0.5 秒（尾音衰減）。
# ---------------------------------------------------------------------------
TRIM_MIN_DR_DB = 15.0
TRIM_MIN_SILENCE_S = 0.6
TRIM_PAD_HEAD_S = 0.3
TRIM_PAD_TAIL_S = 0.5


def trim_silence(audio) -> tuple[object, float, float]:
    """回 (裁過的 audio, 頭裁幾秒, 尾裁幾秒)。條件不符就原樣回傳。"""
    n = len(audio) // _GATE_FRAME
    if n < 10:
        return audio, 0.0, 0.0
    r = np.sqrt(np.square(audio[: n * _GATE_FRAME].reshape(n, _GATE_FRAME)).mean(axis=1)) + 1e-7
    p10, p90 = np.percentile(r, [10, 90])
    if 20 * np.log10(p90 / p10) < TRIM_MIN_DR_DB:
        return audio, 0.0, 0.0              # 沒有安靜底（連續講話／吵雜環境）→ 不裁
    voiced = np.flatnonzero(r > 4 * p10)
    if voiced.size == 0:
        return audio, 0.0, 0.0
    fs = _GATE_FRAME / SAMPLE_RATE
    s0 = max(0, voiced[0] - int(TRIM_PAD_HEAD_S / fs)) * _GATE_FRAME
    s1 = min(n, voiced[-1] + 1 + int(TRIM_PAD_TAIL_S / fs)) * _GATE_FRAME
    if s0 / SAMPLE_RATE < TRIM_MIN_SILENCE_S:
        s0 = 0
    if (len(audio) - s1) / SAMPLE_RATE < TRIM_MIN_SILENCE_S:
        s1 = len(audio)
    if s0 == 0 and s1 == len(audio):
        return audio, 0.0, 0.0
    return audio[s0:s1], s0 / SAMPLE_RATE, (len(audio) - s1) / SAMPLE_RATE


# ---------------------------------------------------------------------------
# 30–33 秒的音檔在中間停頓處對半切（v0.6.0）
#
# Whisper 每 30 秒硬切一窗，30.26 秒的音檔會剩一個 0.26 秒的碎片窗口；
# 重放實測這片碎片被 prompt 續寫成迴圈（壓縮比 10.8）→ 溫度重解 → 單段 ASR 19.9–38 秒。
# 只處理「一定會留下 ≤3 秒碎片」的 30–33 秒區間：在中點 ±3 秒內最安靜的 0.3 秒處切一刀，
# 用 mlx_whisper 的 clip_timestamps 交給它（上下文 prompt 照常跨段延續）。
# ⚠️ 試過全面均分（>30 秒一律切成 ≤24 秒的段）：長句錯字率下降，但有一段 90 秒錄音的
# 第一段被 Whisper 整個跳過 10 秒內容——自己切窗會引入新的失敗型態，所以只留在窄區間。
# ---------------------------------------------------------------------------
CLIP_SLIVER_MIN_S = 30.0
CLIP_SLIVER_MAX_S = 33.0
CLIP_SEARCH_S = 3.0


def asr_clip_timestamps(audio) -> list[float] | None:
    dur = len(audio) / SAMPLE_RATE
    if not (CLIP_SLIVER_MIN_S < dur < CLIP_SLIVER_MAX_S):
        return None
    n = len(audio) // _GATE_FRAME
    r = np.sqrt(np.square(audio[: n * _GATE_FRAME].reshape(n, _GATE_FRAME)).mean(axis=1))
    fs = _GATE_FRAME / SAMPLE_RATE
    lo = int((dur / 2 - CLIP_SEARCH_S) / fs)
    hi = int((dur / 2 + CLIP_SEARCH_S) / fs)
    win = max(1, int(0.3 / fs))      # 最安靜的「0.3 秒區間」，比單幀更不會切在字中間
    seg = np.convolve(r[lo:hi], np.ones(win) / win, mode="same")
    cut = round((lo + int(np.argmin(seg))) * fs, 2)
    return [0.0, cut, cut, round(dur, 2)]


# 殼的逾時公式（AppDelegate.transcribeTimeout，IO-CONTRACT §停損）——daemon 端照抄一份，
# 用來算「還剩多少時間可以花在標點上」。殼等不到回應就斷線，字就沒進游標
# （實測：一段 144 秒的口述 ASR 跑了 34 秒，超過殼的 36 秒上限減去標點時間）。
# SAFETY 留給 socket 往返與殼端處理。
SHELL_TIMEOUT_FLOOR_S = 15.0
SHELL_TIMEOUT_DIV = 6.0
SHELL_TIMEOUT_HEADROOM_S = 12.0
SHELL_DEADLINE_SAFETY_S = 2.0


def shell_deadline_s(dur: float | None) -> float:
    """殼會等多久（秒）扣掉安全邊際。"""
    d = dur or 0.0
    return max(SHELL_TIMEOUT_FLOOR_S, d / SHELL_TIMEOUT_DIV + SHELL_TIMEOUT_HEADROOM_S) - SHELL_DEADLINE_SAFETY_S

# muse_lexicon: import from the selected lexicon root
sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
from muse_lexicon import Lexicon, apply_opencc, smart_punct_zh  # noqa: E402

# initial_prompt 風格種子：繁體範例句，讓 whisper 從解碼端就傾向繁體＋有標點的輸出。
# ⚠️ whisper 解碼器只留 prompt 最後 223 個 token（mlx_whisper decoding.py：
# `prompt_tokens[-(n_ctx // 2 - 1):]`）。v0.5.x 把這句放在開頭、後面接一長串專名，
# 整串超過上限時開頭會被無聲砍掉——風格句與最重要的專名從來沒進過解碼器。
# 現在改成**放在尾端**、整串用 token 預算組（見 build_whisper_prompt）。
# 重放 A/B（有正解的 24 段，不分大小寫錯字率）：舊結構 4.44% ／ 半形逗號無空白 4.17% ／
# 全形逗號無空白 3.79% ／ **全形逗號＋中英間空白 3.28%**（有無固定亂數種子兩次都一樣）。
# 代價：large-v3 會吐小寫形「﹐」「﹖」，由 normalize_small_forms 確定性轉回一般全形。
PUNCT_STYLE_SEED = "好的，我們用 Claude 跟 TouchDesigner 來做，這樣就對了。"
# 必進的核心詞（排在最前面，預算不夠時最後才被擠掉的是詞庫專名）；由 product config 提供，
# 例如使用者自己的名字。公開預設為空。
PROMPT_CORE_TERMS = tuple(t.strip() for t in PROMPT_CORE_TERMS_RAW.split("、") if t.strip())
# whisper 解碼器保留的 prompt 上限是 223 token；200 是 A/B 驗證過的那一版（計法略保守，實際 ~197）
PROMPT_TOKEN_BUDGET = 200

# 優先專名：由 product config 的 PRIORITY_TERMS 提供（「、」分隔），接在核心詞後面。
DICTATE_PRIORITY_TERMS = PRIORITY_TERMS

_PROMPT_TOKENIZER = None


def _prompt_token_len(text: str) -> int:
    """whisper tokenizer 算 token 數（原樣 encode）；拿不到 tokenizer（測試環境）就用字數保守估。"""
    global _PROMPT_TOKENIZER
    if _PROMPT_TOKENIZER is None:
        try:
            from mlx_whisper.tokenizer import get_tokenizer
            _PROMPT_TOKENIZER = get_tokenizer(True, num_languages=100, language="zh", task="transcribe")
        except Exception:  # noqa: BLE001
            _PROMPT_TOKENIZER = False
    if _PROMPT_TOKENIZER:
        return len(_PROMPT_TOKENIZER.encode(text))
    return int(len(text) * 1.3) + 1


def build_whisper_prompt(lex, with_seed: bool = True) -> str:
    """詞表在前、風格種子在尾，整串控制在 PROMPT_TOKEN_BUDGET 內（v0.6.0）。

    詞序＝核心詞 → 生態系優先詞 → 詞庫專名（_canonical 優先）；預算不夠時從後面擠掉。
    with_seed=False ＝ <4 秒錄音用的「只有詞表」版（風格句會被續寫進短句）。
    重放 A/B：有正解的 24 段錯字率 4.44% → 3.28%；真人語段 24 段的 ASR 總耗時
    177 秒 → 70 秒（含解碼護欄）。
    """
    pool: list[str] = []
    seen: set[str] = set()
    names = [t.strip() for t in lex.build_initial_prompt(max_chars=600).split(",")]
    for t in (*PROMPT_CORE_TERMS, *DICTATE_PRIORITY_TERMS.split("、"), *names):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            pool.append(t)
    tail = "。" + PUNCT_STYLE_SEED if with_seed else "。"
    used = _prompt_token_len(" " + tail.lstrip("。")) + 2     # +2：句號與 encode 前綴空白
    chosen: list[str] = []
    for t in pool:
        cost = _prompt_token_len("、" + t)
        if used + cost > PROMPT_TOKEN_BUDGET:
            continue
        chosen.append(t)
        used += cost
    return "、".join(chosen) + tail


# ---------------------------------------------------------------------------
# LLM 標點修復（punct="llm_zh"，v0.3）— project rule「快速 LLM 產生標點，但絕不咬我的字」
# 硬保證（不是信任是閘門）：輸出經 opencc s2t 正規化後，「非標點字元序列」必須與輸入
# 完全一致——LLM 動到任何一個字＝整段丟棄、退回規則層 smart_punct_zh。
# ---------------------------------------------------------------------------
PUNCT_LLM_URL = env("PUNCT_LLM_URL", "http://127.0.0.1:11434/api/chat")
PUNCT_LLM_MODEL = env("PUNCT_MODEL", "qwen3.6:35b-a3b-coding-nvfp4")
# keep_alive：實測過的失敗鏈——預設 30m 過期後，下一句要付整顆模型的冷載成本，
# 直接吃光逾時預算，於是「久沒用的第一句」必定退回規則層。設 24h 常駐可根治；
# 記憶體吃緊的機器可以調回較短值，代價就是冷載那一句。
PUNCT_LLM_KEEP_ALIVE = env("KEEP_ALIVE", "24h")
PUNCT_LLM_MAX_CHARS = 800   # 超長段直接走規則層（延遲考量）

# 逾時預算：長度感知（v0.5.3 起），v0.6.0 重新定值
#
# LLM 要重寫整段，decode 成本隨文長線性成長 ⇒ 預算必須隨長度走。
# ⚠️ 誠實標：長度**不是**逾時的唯一成因。把曾逾時的樣本原樣重跑，二十字的句子只要約 0.3 秒——
# 短句不缺時間，它們是被兩件事卡住的：①模型不在記憶體（見下方 PunctWarmer）②整機資源競爭。
# 所以 except 分支會把字數／預算／實耗／整機負載印在逾時那一行，讓下一次逾時當場留證據。
#
# v0.6.0：base 1.5s、每字 0.012s、CAP 5s。成功樣本 180–260 字 p90 約 1.9 秒（≈9ms/字），
# 整機高負載時 212 字要 3.1 秒（≈15ms/字）。殼逾時已由 daemon 端截止時間守住（shell_deadline_s），
# CAP 不再需要替殼扛安全邊際。
PUNCT_LLM_TIMEOUT_BASE_S = float(env("PUNCT_BASE_S", "1.5"))
PUNCT_LLM_TIMEOUT_PER_CHAR_S = float(env("PUNCT_PER_CHAR_S", "0.012"))
# ⚠️ CAP 跟殼的逾時是一組契約：殼 `transcribeTimeout()` = max(15, 音檔秒/6 + 12)。
# v0.6.0 起 daemon 自己算殼的截止時間，每次送 LLM 前把預算夾在剩餘時間內——
# 就算 CAP 設錯，也不會再出現「殼斷線、daemon 在背景跑完」。
PUNCT_LLM_TIMEOUT_CAP_S = float(env("PUNCT_CAP_S", "5.0"))
PUNCT_MIN_BUDGET_S = 0.8   # v0.6.0：距殼逾時少於這個就不打 LLM（warm 短句 p50 ~0.4s）

# 程序內 LLM 標點斷路器：連線／逾時不可用時，後續句子先走既有規則層，
# 不要每句重新等一個已知失效的 urllib budget。用 monotonic clock，避免系統時間跳動。
try:
    PUNCT_COOLDOWN_S = max(0.0, float(env("PUNCT_COOLDOWN_S", "30")))
except (TypeError, ValueError):
    PUNCT_COOLDOWN_S = 30.0
_punct_circuit_open_until = 0.0
_punct_circuit_notice_until = 0.0


def _punct_circuit_remaining() -> float:
    """回傳冷卻剩餘秒數；已過期就關閘。daemon 單執行緒，無需鎖。"""
    global _punct_circuit_open_until, _punct_circuit_notice_until
    remaining = _punct_circuit_open_until - time.monotonic()
    if remaining <= 0:
        _punct_circuit_open_until = 0.0
        _punct_circuit_notice_until = 0.0
        return 0.0
    return remaining


def _trip_punct_circuit(exc: BaseException) -> None:
    """只對 transport/timeout 類失敗熔斷；閘門失敗不會走到這裡。"""
    global _punct_circuit_open_until, _punct_circuit_notice_until
    _punct_circuit_open_until = time.monotonic() + PUNCT_COOLDOWN_S
    _punct_circuit_notice_until = _punct_circuit_open_until
    log_line(f"llm_punct circuit open（{exc.__class__.__name__}，冷卻 {PUNCT_COOLDOWN_S:.1f}s）")

# 分段標點（v0.5.4）：長口述在單次預算內**結構上不可能完成**（LLM 要重新輸出全文），
# 而 Whisper 對長段連續語音常整段不吐標點、規則層只正規化不斷句
# ⇒ 最長最有價值的口述拿到的是零標點的牆。解法是切成每段都能在預算內完成的大小、逐段標點：
#   - 每段 ~CHUNK 字；切點優先自然邊界、英數段不切開，保證 join 逐字還原
#   - 斷路器：某段「吃滿預算」失敗（stall）⇒ 其餘段不再等 LLM，直接規則層；
#     閘門失敗＝LLM 活著但亂改字 ⇒ 不熔斷，只犧牲那一段
#   - 部分成功記 llm_zh_partial：有標點的段 > 沒標點的牆
# ⚠️ 誠實標：硬切點落在語意中間時，LLM 可能在段尾補一個不該有的句號（縫合處錯標點）。
PUNCT_CHUNK_CHARS = int(env("PUNCT_CHUNK_CHARS", "220"))
PUNCT_CHUNK_SLACK = 40   # 切點搜尋窗：target±SLACK 內找自然邊界
PUNCT_LONG_MAX_CHARS = int(env("PUNCT_LONG_MAX", "3000"))


def punct_timeout_for(n_chars: int) -> float:
    """長度感知逾時預算（秒）。40 字→2.0s／150 字→3.3s／220 字→4.1s／292 字以上→cap 5s。
    實際送出前還會被殼截止時間再夾一次（llm_punct_and_fix 的 deadline）。"""
    return min(PUNCT_LLM_TIMEOUT_BASE_S + n_chars * PUNCT_LLM_TIMEOUT_PER_CHAR_S,
               PUNCT_LLM_TIMEOUT_CAP_S)


def _machine_load() -> str:
    """逾時當下的整機負載快照（load average + 記憶體壓力）。純唯讀、失敗不影響主流程。"""
    try:
        l1, l5, _ = os.getloadavg()
        parts = [f"load {l1:.1f}/{l5:.1f}"]
    except OSError:
        parts = ["load ?"]
    try:
        import subprocess
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            if "percentage" in line.lower():
                parts.append(line.strip())
                break
    except Exception:  # noqa: BLE001 — 診斷用，壞掉就少一欄，不能影響聽寫
        pass
    return "，".join(parts)
# ---------------------------------------------------------------------------
# LLM 常駐看守（v0.6.0）
#
# 失敗機制（Ollama server log 實證）：模型不在記憶體 → 我們的請求觸發載入（~21GB）
# → 1.5–3.6 秒預算到了、urllib 斷線 → Ollama 記 `error loading llama server: context canceled`
# 並停掉載入中的 runner。請求一斷線載入就跟著被取消，所以模型**永遠**載不起來：
# 閒置過後的每一句都退回規則層，而且不會自己好。另一半：剛載入完的前幾次推論也慢。
#
# 解法是把「載入」從使用者的請求路徑拿掉：
#   ① 背景執行緒用長逾時負責載入＋暖身推論（載入不會再被取消）
#   ② 請求路徑先看模型在不在；不在 → 立刻走規則層並叫醒看守，不再白等
#   ③ 看守每 PUNCT_WARM_INTERVAL_S 秒用 /api/ps 查一次（~10ms），被卸載就重新載入
#   ④ 防抖：載入後很快又被別的模型擠掉 → 退避（10 分、20 分…上限 2 小時）
# 關掉：<PREFIX>_PUNCT_WARM=0（回到 v0.5.4 行為）。代價：LLM 常駐佔記憶體。
# ---------------------------------------------------------------------------
PUNCT_WARM_ENABLED = env("PUNCT_WARM", "1") != "0"
PUNCT_WARM_INTERVAL_S = float(env("PUNCT_WARM_INTERVAL_S", "120"))
PUNCT_WARM_LOAD_TIMEOUT_S = 240.0
PUNCT_READY_TTL_S = 15.0          # 確認「在」之後 15 秒內不再查；超過就當場查 /api/ps（~10ms），外部卸載也抓得到
PUNCT_PS_TIMEOUT_S = 0.4
PUNCT_WARM_BACKOFF_MIN_S = 600.0
PUNCT_WARM_BACKOFF_MAX_S = 7200.0
PUNCT_EVICTED_SOON_S = 600.0      # 載入後這麼快就不見 ＝ 被擠掉，不是自然過期


def _ollama_base() -> str:
    from urllib.parse import urlsplit
    u = urlsplit(PUNCT_LLM_URL)
    return f"{u.scheme}://{u.netloc}"


def punct_model_loaded(timeout: float = PUNCT_PS_TIMEOUT_S) -> bool | None:
    """/api/ps 查模型在不在記憶體。True／False；查不到（Ollama 沒開）回 None。"""
    try:
        with urllib.request.urlopen(_ollama_base() + "/api/ps", timeout=timeout) as r:
            models = json.load(r).get("models") or []
    except Exception:  # noqa: BLE001 — 查不到就當不知道，交給呼叫端決定
        return None
    want = PUNCT_LLM_MODEL if ":" in PUNCT_LLM_MODEL else PUNCT_LLM_MODEL + ":latest"
    return any(m.get("name") == want or m.get("model") == want for m in models)


class PunctWarmer:
    """背景看守 LLM 標點模型常駐。daemon 單執行緒處理請求；這條執行緒只打 Ollama HTTP。"""

    def __init__(self) -> None:
        import threading
        self._kick = threading.Event()
        self._thread = threading.Thread(target=self._run, name="punct-warmer", daemon=True)
        self.ready_at = 0.0          # 最近一次確認「在」的 monotonic 時間
        self.loading = False
        self.last_load_s: float | None = None
        self.last_error: str | None = None
        self._loaded_at = 0.0
        self._backoff_s = 0.0
        self._next_try = 0.0

    def start(self) -> None:
        if PUNCT_WARM_ENABLED:
            self._thread.start()

    def kick(self) -> None:
        self._kick.set()

    def mark_ready(self) -> None:
        self.ready_at = time.monotonic()

    def is_ready(self) -> bool:
        """請求路徑用：最近確認過就信，不然當場查一次（~10ms）。"""
        if not PUNCT_WARM_ENABLED:
            return True                # 關掉看守 ＝ 舊行為：直接打，讓逾時自己處理
        if self.loading:
            return False
        if time.monotonic() - self.ready_at < PUNCT_READY_TTL_S:
            return True
        loaded = punct_model_loaded()
        if loaded:
            self.mark_ready()
            return True
        self.kick()
        return False                   # 不在（或 Ollama 沒開）→ 這句走規則層，看守去載

    def status(self) -> dict:
        age = time.monotonic() - self.ready_at if self.ready_at else None
        return {"enabled": PUNCT_WARM_ENABLED, "loading": self.loading,
                "ready_age_s": round(age, 1) if age is not None else None,
                "last_load_s": self.last_load_s, "last_error": self.last_error}

    # ---------------------------------------------------------------- thread
    def _run(self) -> None:
        while True:
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001 — 看守壞掉不能拖垮聽寫
                self.last_error = repr(e)
                log_line(f"⚠️ punct warmer error: {e!r}")
            self._kick.wait(PUNCT_WARM_INTERVAL_S)
            self._kick.clear()

    def _tick(self) -> None:
        loaded = punct_model_loaded(timeout=5.0)
        now = time.monotonic()
        if loaded is None:
            self.last_error = "ollama_unreachable"
            return
        if loaded:
            self.mark_ready()
            return
        if now < self._next_try:
            return
        if self._loaded_at and now - self._loaded_at < PUNCT_EVICTED_SOON_S:
            self._backoff_s = min(max(self._backoff_s * 2, PUNCT_WARM_BACKOFF_MIN_S),
                                  PUNCT_WARM_BACKOFF_MAX_S)
            self._next_try = now + self._backoff_s
            log_line(f"llm_punct warm：載入 {now - self._loaded_at:.0f}s 後就被卸載"
                     f"（多半是別的模型要記憶體）→ 退避 {self._backoff_s / 60:.0f} 分")
            self._loaded_at = 0.0
            return
        self._load()

    def _load(self) -> None:
        """長逾時載入＋暖身推論（用實際 prompt 的開頭，順便暖 prompt cache）。"""
        self.loading = True
        t0 = time.perf_counter()
        try:
            body = {
                "model": PUNCT_LLM_MODEL, "think": False, "stream": False,
                "keep_alive": PUNCT_LLM_KEEP_ALIVE,
                "messages": [{"role": "user",
                              "content": PUNCT_LLM_PROMPT_HEAD + PUNCT_LLM_PROMPT_TAIL + "好的我知道了謝謝"}],
                "options": {"temperature": 0, "num_predict": 16},
            }
            for _ in range(2):   # 第二次＝剛載入完的慢推論，在背景先吃掉
                req = urllib.request.Request(PUNCT_LLM_URL, data=json.dumps(body).encode("utf-8"),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=PUNCT_WARM_LOAD_TIMEOUT_S) as r:
                    r.read()
            self.last_load_s = round(time.perf_counter() - t0, 1)
            self.last_error = None
            self._loaded_at = time.monotonic()
            self._backoff_s = 0.0
            self.mark_ready()
            log_line(f"llm_punct warm：{PUNCT_LLM_MODEL} 載入＋暖身 {self.last_load_s}s")
        except Exception as e:  # noqa: BLE001
            self.last_error = f"load_failed {e.__class__.__name__}"
            self._next_try = time.monotonic() + PUNCT_WARM_BACKOFF_MIN_S
            log_line(f"⚠️ llm_punct warm 載入失敗（{e!r}）→ {PUNCT_WARM_BACKOFF_MIN_S / 60:.0f} 分後再試")
        finally:
            self.loading = False


PUNCT_WARMER = PunctWarmer()


PUNCT_LLM_PROMPT_HEAD = (
    "為下面文字修復繁體中文標點（該用頓號用頓號、對話加「」引號、列舉用冒號、保留原有正確標點）。"
    "只能插入或替換標點符號，絕對不能改動、增加或刪除任何字。"
)
PUNCT_LLM_PROMPT_CTX = (
    "唯一例外：下方「已知語音誤聽對照表」裡的詞，若語境明顯是右側的意思，修正為右側；"
    "語境不符或不確定就保留原字，絕不套用表外的任何修正。\n對照表：{table}\n"
)
PUNCT_LLM_PROMPT_TAIL = "直接輸出結果，不要任何說明。\n\n"


def _reachable_by_pairs(a: str, b: str, pairs: list[tuple[str, str]]) -> bool:
    """閘門 v2 核心：content 字串 b 是否可由 a「僅」透過在任意位置套用授權 pair 得到。

    雙指針 + 記憶化回溯（狀態 ≤ len(a)×len(b) 稀疏；文字 <800 字、pairs <30 → 便宜）。
    單字偷換（彈→談）不可能通過：pair 以全詞儲存（來彈→來談），逐字前進時
    只有整組 wrong→right 對齊才走得下去。
    """
    if a == b:
        return True
    import sys as _sys
    _sys.setrecursionlimit(max(_sys.getrecursionlimit(), len(a) + len(b) + 100))
    from functools import lru_cache

    @lru_cache(maxsize=None)
    def ok(i: int, j: int) -> bool:
        if i == len(a) and j == len(b):
            return True
        if i < len(a) and j < len(b) and a[i] == b[j] and ok(i + 1, j + 1):
            return True
        for w, r in pairs:
            if w and r and a.startswith(w, i) and b.startswith(r, j) and ok(i + len(w), j + len(r)):
                return True
        return False

    return ok(0, 0)


def llm_punct_and_fix(text: str, contextual_pairs: list[tuple[str, str]],
                      safe_pairs: list[tuple[str, str]],
                      deadline: float | None = None) -> str | None:
    """LLM 標點修復 + 受控語境錯字修正（punct="llm_zh"，daemon v0.4）。

    project rule：「頂多做格式跟錯字校正，其他都不要動。」保證不靠信任靠閘門：
    輸出經 opencc 正規化後，其 content 序列必須可由輸入 content「僅套用授權 pair」重建
    （授權 = 詞庫 _contextual + 安全 replacements 的純字串 pair）。任何表外變動 → 丟棄 fallback。
    """
    global _punct_circuit_notice_until
    if not text or len(text) > PUNCT_LLM_MAX_CHARS:
        return None
    remaining = _punct_circuit_remaining()
    if remaining > 0:
        if _punct_circuit_notice_until != _punct_circuit_open_until:
            _punct_circuit_notice_until = _punct_circuit_open_until
            log_line(f"llm_punct circuit open（剩餘 {remaining:.1f}s）→ fallback 規則層")
        return None
    # v0.6.0：模型不在記憶體就不打（打了只會白等、還會把載入取消掉），看守在背景載
    if not PUNCT_WARMER.is_ready():
        log_line(f"llm_punct 模型未就緒（{'載入中' if PUNCT_WARMER.loading else '不在記憶體'}）"
                 f"→ 這句走規則層，看守背景載入")
        return None
    budget_s = punct_timeout_for(len(text))
    # v0.6.0：殼的逾時是硬牆——剩下的時間不夠就別打，先把字交出去
    if deadline is not None:
        left = deadline - time.monotonic()
        if left < PUNCT_MIN_BUDGET_S:
            log_line(f"llm_punct 跳過：距殼逾時只剩 {left:.1f}s（{len(text)} 字）→ 規則層")
            return None
        budget_s = min(budget_s, left)
    # v0.6.0：對照表只帶「誤聽那一側真的出現在這句裡」的 pair。
    # 語境詞表被批量匯入長到約一千組時，每句 prompt 變成 ~6,100 token：
    # prompt cache 冷的時候光 prompt eval 就 6.8 秒（實測），預算必逾時；熱的時候 0.4 秒。
    # 沒出現在句子裡的 pair 本來就不可能被套用 ⇒ 閘門結果完全等價，prompt 回到一兩百 token。
    text_cc = _content_chars(text)
    contextual_pairs = [(w, r) for w, r in contextual_pairs if _content_chars(w) and _content_chars(w) in text_cc]
    safe_pairs = [(w, r) for w, r in safe_pairs if _content_chars(w) and _content_chars(w) in text_cc]
    prompt = PUNCT_LLM_PROMPT_HEAD
    if contextual_pairs:
        table = "、".join(f"{w}→{r}" for w, r in contextual_pairs)
        prompt += PUNCT_LLM_PROMPT_CTX.format(table=table)
    prompt += PUNCT_LLM_PROMPT_TAIL + text
    body = {
        "model": PUNCT_LLM_MODEL, "think": False, "stream": False,
        "keep_alive": PUNCT_LLM_KEEP_ALIVE,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0, "num_predict": max(64, len(text) * 2)},
    }
    req = urllib.request.Request(PUNCT_LLM_URL, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=budget_s) as r:
            out = (json.load(r).get("message") or {}).get("content", "").strip()
        ms = int((time.perf_counter() - t0) * 1000)
    except urllib.error.HTTPError as e:
        # HTTP 回應代表 transport 仍可達；保留既有 fallback，但不要把閘門／服務
        # 回應誤當成「連線斷路」而擴大冷卻範圍。
        spent = int((time.perf_counter() - t0) * 1000)
        log_line(f"llm_punct 不可用（HTTP {e.code}，{len(text)} 字／"
                 f"預算 {budget_s:.1f}s／實耗 {spent}ms）→ fallback 規則層")
        return None
    except (urllib.error.URLError, ConnectionError, OSError, TimeoutError) as e:
        # 逾時當場留證據，不要事後推理（2026-08-01 教訓：為了查 8 次逾時的成因，
        # 得回頭翻 fleet history 的 RAM 曲線，還先後推翻自己兩個假設）。
        # 字數/預算/實耗 → 分辨「預算不夠」vs「被卡住」；機器負載 → 分辨「模型慢」vs「整機忙」。
        spent = int((time.perf_counter() - t0) * 1000)
        _trip_punct_circuit(e)
        PUNCT_WARMER.ready_at = 0.0      # 下一句重新確認模型還在不在
        PUNCT_WARMER.kick()
        log_line(f"llm_punct 不可用（{e.__class__.__name__}，{len(text)} 字／"
                 f"預算 {budget_s:.1f}s／實耗 {spent}ms／{_machine_load()}）→ fallback 規則層")
        return None
    except json.JSONDecodeError as e:
        # LLM 有回應但格式壞掉，不是連線型不可用；下一句仍可重試。
        spent = int((time.perf_counter() - t0) * 1000)
        log_line(f"llm_punct 回應解析失敗（{e.__class__.__name__}，實耗 {spent}ms）"
                 "→ fallback 規則層")
        return None
    out = apply_opencc(out)  # 部分模型傾向輸出簡體 → 正規化後再進閘門
    if not out:
        log_line(f"llm_punct 空輸出 → fallback（{ms}ms）")
        return None
    allowed = [(_content_chars(w), _content_chars(r)) for w, r in (contextual_pairs + safe_pairs)]
    allowed = [(w, r) for w, r in allowed if w and r and w != r]
    if not _reachable_by_pairs(_content_chars(text), _content_chars(out), allowed):
        log_line(f"llm_punct 閘門 v2 未過（出現表外變動）→ fallback（{ms}ms）")
        return None
    PUNCT_WARMER.mark_ready()
    log_line(f"llm_punct ok（{ms}ms）")
    return out


_ALNUM_RUN = re.compile(r"[A-Za-z0-9._%/+\-]")
_CUT_STRONG = "。！？!?；;\n"
_CUT_WEAK = "，、, 　"


def _split_for_punct(text: str, target: int | None = None,
                     slack: int | None = None) -> list[str]:
    """長文切段給 LLM 標點。優先切在句末標點後，其次逗頓／空白後，
    再不然避開英數連續段（TouchDesigner／3,000／README.md 不從中間切）硬切。
    保證 ``"".join(結果) == text``——切段只決定邊界，不動任何字。"""
    target = target or PUNCT_CHUNK_CHARS
    slack = slack or PUNCT_CHUNK_SLACK
    chunks, i, n = [], 0, len(text)
    while n - i > target + slack:
        lo, hi = i + target - slack, min(i + target + slack, n - 1)
        cut = -1
        for pool in (_CUT_STRONG, _CUT_WEAK):
            for k in range(hi, lo - 1, -1):
                if text[k - 1] in pool:
                    cut = k
                    break
            if cut > 0:
                break
        if cut < 0:
            cut = i + target
            while cut > lo and _ALNUM_RUN.match(text[cut - 1]) and _ALNUM_RUN.match(text[cut]):
                cut -= 1
        chunks.append(text[i:cut])
        i = cut
    chunks.append(text[i:])
    return chunks


def llm_punct_routed(text: str, contextual_pairs: list[tuple[str, str]],
                     safe_pairs: list[tuple[str, str]],
                     deadline: float | None = None) -> tuple[str, str]:
    """llm_zh 模式總路由（v0.5.4）：短文單發；長文分段＋stall 斷路器。

    v0.6.0：deadline（monotonic 絕對時間）＝殼逾時減安全邊際；每段開打前看一次，
    時間不夠的段直接走規則層（有標點的前半段＋規則層後半段 > 殼斷線整段掉）。

    回 (輸出文字, punct_mode)，punct_mode ∈ llm_zh／llm_zh_partial／smart_zh_fallback。
    """
    if len(text) <= PUNCT_CHUNK_CHARS + PUNCT_CHUNK_SLACK:
        fixed = llm_punct_and_fix(text, contextual_pairs, safe_pairs, deadline)
        if fixed is not None:
            return fixed, "llm_zh"
        return smart_punct_zh(text), "smart_zh_fallback"
    if len(text) > PUNCT_LONG_MAX_CHARS:
        return smart_punct_zh(text), "smart_zh_fallback"
    chunks = _split_for_punct(text)
    out: list[str] = []
    ok_n, stalled = 0, False
    for c in chunks:
        if not stalled and _punct_circuit_remaining() > 0:
            stalled = True
        if not stalled and deadline is not None and deadline - time.monotonic() < PUNCT_MIN_BUDGET_S:
            stalled = True
            log_line("llm_punct 分段：距殼逾時不足 → 剩餘段走規則層")
        if not stalled and PUNCT_WARM_ENABLED and not PUNCT_WARMER.is_ready():
            stalled = True
            log_line("llm_punct 分段：模型未就緒 → 全段走規則層，看守背景載入")
        if not stalled:
            t0 = time.perf_counter()
            fixed = llm_punct_and_fix(c, contextual_pairs, safe_pairs, deadline)
            spent = time.perf_counter() - t0
            if fixed is not None:
                out.append(fixed)
                ok_n += 1
                continue
            # 失敗分流：吃滿預算＝stall（熔斷，別讓後面的段陪葬時間）；
            # 快速失敗＝閘門/空輸出（LLM 活著，下一段照試）。
            if _punct_circuit_remaining() > 0 or spent >= punct_timeout_for(len(c)) * 0.9:
                stalled = True
        out.append(smart_punct_zh(c))
    if ok_n == 0:
        return "".join(out), "smart_zh_fallback"
    mode = "llm_zh" if ok_n == len(chunks) else "llm_zh_partial"
    log_line(f"llm_punct 分段 {ok_n}/{len(chunks)} ok"
             + ("（斷路器熔斷）" if stalled else "") + f" → {mode}")
    return "".join(out), mode


import numpy as np  # noqa: E402  (mlx_whisper 依賴，venv 必有)


def log_line(msg: str) -> None:
    """daemon 運維 log → stderr（launchd 收進 daemon.err.log）。"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# 音訊：契約格式 PCM16 wav 直接用 stdlib 解（省 ffmpeg subprocess ~數十 ms）
# ---------------------------------------------------------------------------
def load_wav(path: str):
    """回傳 (audio_float32 | None, duration_s | None)。

    契約格式（16k/mono/PCM16）→ numpy 直解。
    非契約格式 → 回 (None, dur)，上層 fallback 丟檔案路徑給 mlx_whisper（走 ffmpeg）。
    """
    with wave.open(path, "rb") as w:
        rate, channels, width, frames = (
            w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes(),
        )
        dur = frames / rate if rate else 0.0
        if rate != SAMPLE_RATE or width != 2:
            return None, dur  # 非契約格式，交給 ffmpeg 重採樣
        data = w.readframes(frames)
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, dur


_TMP_PREFIXES = ("/tmp/", "/private/tmp/")


def cleanup_wav(path: str) -> None:
    """處理完刪除 /tmp 下的 wav；非 /tmp 路徑不刪（契約）。macOS /tmp → /private/tmp。"""
    try:
        real = os.path.realpath(path)
        if not (path.startswith(_TMP_PREFIXES) or real.startswith(_TMP_PREFIXES)):
            return
        if not real.endswith(".wav"):
            return
        os.unlink(real)
    except OSError as e:
        log_line(f"⚠️ cleanup_wav failed for {path}: {e}")


# ---------------------------------------------------------------------------
# no_speech 判定
# ---------------------------------------------------------------------------
def _content_chars(text: str) -> str:
    """去掉標點/空白/符號/控制字元，只留字母數字（含 CJK）。"""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "Z", "S", "C"))


def is_no_speech(result: dict) -> bool:
    """segments 全空、或全部 segment no_speech_prob 高（>0.6）且文字空白/純標點。"""
    segments = result.get("segments") or []
    seg_texts = [(seg.get("text") or "").strip() for seg in segments]
    if not segments or not any(seg_texts):
        return True
    all_high = all(float(seg.get("no_speech_prob", 0.0)) > NO_SPEECH_PROB for seg in segments)
    if all_high and not _content_chars(result.get("text") or ""):
        return True
    return False


# ---------------------------------------------------------------------------
# 迴圈幻覺「尾巴」層（v0.5.3）
#
# Whisper 在極短音訊與長音訊兩端都會陷入 token 迴圈，把同一個字或片語重複到
# 視窗結束（「多多多多…」「安安安安」「好，我們來看看…」×N）。實測分佈是雙峰的：
# <3s 與 >=30s 兩端各有約 20% 的輸出被污染，中間長度幾乎不受影響。
#
# 為什麼放在 daemon 而不是 lexicon 引擎：`Lexicon.correct()` 的行內迴圈收斂
# 刻意把迴圈**收斂成一份**而不是刪掉（它的 docstring 明寫「lib 不做整行移除，
# 殘渣去留交上層」），所以長口述尾巴會固定掛一個殘字。daemon 就是那個上層。
#
# 為什麼要在 lex.correct() **之前**跑：correct() 一旦收斂完，迴圈就不存在了，
# 這裡只會看到一個殘字，判不出來。順序是這層的正確性條件，不是風格選擇。
#
# 門檻取捨（守「寧可漏改，不可錯改」）：
#   - 只處理貼著結尾的迴圈；句中重複交給 lexicon 保守的門檻
#   - 前面有實質內容時 4 次即算失控；內容很短時拉到 6 次
#   - token 上限 40 而非 12：長片語迴圈（十多個字重複四次）用 12 會直接漏掉
#   - 「加油加油加油」「對對對對」「嗯嗯嗯」這類正常強調不受影響
# ---------------------------------------------------------------------------
TAIL_LOOP = re.compile(r"(.{1,40}?)(?:[,，、。\.\s]?\1){2,}[。\.!！?？,，、\s]*$")
TAIL_MIN_BODY = 8            # 前面剩這麼多字才算「有實質內容」
TAIL_REPS_WITH_BODY = 4      # 有實質內容時，重複四次就算失控
TAIL_REPS_SHORT_BODY = 6     # 內容很短時門檻拉高，避免誤傷「我說好好好好」這種
TAIL_SHORT_BODY_MIN_SPAN = 16
# 短 body 的第二觸發：迴圈本身夠長就算失控，不必等到六次。
# 理由是次數門檻對長 token 不公平——「four」重複五次＝20 個字的垃圾，
# 但只算五次；而「好好好好」只有四個字，同樣四次卻是正常語氣。
# 量「垃圾佔了多長」比量「重複幾次」更貼近我們真正想擋的東西。
_WORDISH = re.compile(r"[\w㐀-䶿一-鿿]")


# ---------------------------------------------------------------------------
# 已知的整句幻覺（v0.6.0）
#
# Whisper 訓練資料裡的字幕署名，在靜音／底噪上會被原封不動吐出來。
# 用真實錄音的房間底噪切 24 段 0.6–1.8 秒片段（＝誤觸的樣子）重放，
# 舊版 24 段全部產出文字、沒有一段被判 no_speech：
#   「MING PAO CANADA // MING PAO TORONTO」9 段、「詞曲 李宗盛」5 段、
#   「字幕由 Amara.org 社群提供」「本歌曲来自〖云上工作室〗」、單獨一個「。」……
#
# 規則只在「整段輸出就是這些署名」時才生效（去掉署名後沒剩任何內容）；
# 句尾黏著的署名只砍署名本身。比對前先轉繁體、去空白標點、轉大寫，
# 所以簡繁、全半形、空格位置的變化都吃得到。守「寧可漏改」：
# 只收錄有實際輸出或重放證據的字串，不猜。
# ---------------------------------------------------------------------------
_HALLU_PHRASES_RAW = (
    # 長的放前面：同一段結尾先吃完整署名，再吃片段。簡繁兩種都列——
    # 有 opencc 時會自動對齊，列兩份是讓沒裝 opencc 的環境也對。
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目",
    "明镜与点点栏目", "明鏡與點點欄目", "点点栏目", "點點欄目",
    "优优独播剧场——YoYo Television Series Exclusive", "優優獨播劇場——YoYo Television Series Exclusive",
    "优优独播剧场", "優優獨播劇場",
    "字幕由 Amara.org 社群提供", "字幕由 Amara.org 社区提供", "字幕由 Amara.org 社區提供", "Amara.org",
    "字幕志愿者 李宗盛", "字幕志願者 李宗盛", "字幕志愿者", "字幕志願者",
    "词曲 李宗盛", "詞曲 李宗盛", "作词 李宗盛", "作詞 李宗盛", "作曲 李宗盛", "李宗盛",
    "本歌曲来自〖云上工作室〗", "本歌曲來自〖雲上工作室〗", "云上工作室", "雲上工作室",
    "MING PAO CANADA", "MING PAO TORONTO", "MING PAO",
    "本集完",
)
# 只在「整段就是它」時才算：真的在講李宗盛（「這首歌詞曲李宗盛」）不能被當署名砍掉
_HALLU_WHOLE_ONLY_RAW = ("李宗盛", "词曲 李宗盛", "詞曲 李宗盛", "作词 李宗盛", "作詞 李宗盛",
                         "作曲 李宗盛", "本集完")


def _hallu_norm(text: str) -> str:
    """轉繁（跟校正層同一個 opencc）→ 只留內容字 → 大寫。署名清單也走同一條，簡繁變體自動對齊。"""
    return _content_chars(apply_opencc(text or "")).upper()


_HALLU_PHRASES = tuple(dict.fromkeys(_hallu_norm(p) for p in _HALLU_PHRASES_RAW))
_HALLU_WHOLE_ONLY = tuple(_hallu_norm(p) for p in _HALLU_WHOLE_ONLY_RAW)


def strip_known_hallucination(text: str) -> tuple[str, str | None]:
    """整段是字幕署名 → 回 ("", why)；結尾黏著署名 → 砍掉那段；其他原樣。"""
    norm = _hallu_norm(text)
    if not norm:
        return "", "empty_content"           # 只有標點（例：單獨一個「。」）
    rest = norm
    hit = []
    changed = True
    while changed and rest:
        changed = False
        for ph in _HALLU_PHRASES:
            if rest.endswith(ph) and (ph not in _HALLU_WHOLE_ONLY or rest == ph):
                rest = rest[: -len(ph)]
                hit.append(ph)
                changed = True
                break
    if not hit:
        return text, None
    if not rest:
        return "", f"known_hallucination {'+'.join(reversed(hit))}"
    # 結尾黏著署名：在原文裡從後往前找到署名開始的位置再截斷（保留原文的標點與空白）
    keep, seen = len(text), 0
    target = len(norm) - len(rest)       # 要砍掉的 content 字數
    for i in range(len(text) - 1, -1, -1):
        if _content_chars(text[i]):
            seen += 1
            if seen == target:
                keep = i
                break
    return text[:keep].rstrip(), f"known_hallucination_tail {'+'.join(reversed(hit))}"


_JUNK_BODY = re.compile(r"[\d.\s,，。、:：;；\-]*")
TAIL_WINDOW = 3000   # 單窗迴圈最長 ~224 token ≈ 900 字；長口述上限 PUNCT_LONG_MAX 也是 3000


def _is_sep(ch: str) -> bool:
    """TAIL_LOOP 的分隔符類＋尾巴類（含驚嘆號問號）：這些字連成一串就是回溯炸彈。"""
    return ch in ",，、。.!！?？" or ch.isspace()


def strip_tail_hallucination(text: str) -> tuple[str, str | None]:
    """砍掉貼著結尾的迴圈幻覺。回傳 (文字, 說明)；文字為空 = 呼叫端應判 no_speech。

    只處理「尾巴」——句中重複維持原樣，交給 muse_lexicon 保守的 INLINE_LOOP。
    """
    s = (text or "").rstrip()
    # v0.6.0：TAIL_LOOP 對「一串分隔符＋一個非分隔字」會災難性回溯——30 個逗號／空白 0.4 秒、
    # 40 個 >20 秒（重放時真的卡死過一次；daemon 單執行緒 ＝ 聽寫全停）。
    # 先把連續分隔符壓成一個再比對（記住每個字在原文的位置，切點映射回原文，不改動使用者的字），
    # 並只看最後 TAIL_WINDOW 字：它本來就只管尾巴。壓完之後最壞是平方級，對抗輸入 2000 字 0.04 秒。
    keep_idx = [i for i, ch in enumerate(s)
                if not (i and _is_sep(ch) and _is_sep(s[i - 1]))]
    lo = max(0, len(keep_idx) - TAIL_WINDOW)
    keep_idx = keep_idx[lo:]
    c = "".join(s[i] for i in keep_idx)
    m = TAIL_LOOP.search(c)
    if not m:
        return text, None
    tok = m.group(1)
    if not tok.strip() or not _WORDISH.search(tok):
        return text, None                       # 純標點/空白的重複不碰
    reps = len(re.findall(re.escape(tok), m.group()))
    body = s[: keep_idx[m.start()]].strip()
    if len(body) >= TAIL_MIN_BODY:
        if reps >= TAIL_REPS_WITH_BODY:
            return body, f"tail_loop {tok!r}x{reps} ({len(m.group())} chars)"
        return text, None
    if reps >= TAIL_REPS_SHORT_BODY or len(m.group()) >= TAIL_SHORT_BODY_MIN_SPAN:
        # v0.6.0：迴圈前面只剩數字標點（重放實測「4.4 on on on…」→ 砍完剩「4.4」）
        # ＝整段都是幻覺，不要把「4.4」打進游標。
        if body and _JUNK_BODY.fullmatch(body):
            return "", f"all_loop {tok!r}x{reps} (junk body {body!r})"
        if len(body) >= 2:
            return body, f"tail_loop {tok!r}x{reps} (short body, span {len(m.group())})"
        return "", f"all_loop {tok!r}x{reps}"
    return text, None


# ---------------------------------------------------------------------------
# Daemon 本體
# ---------------------------------------------------------------------------
class DictationDaemon:
    def __init__(self):
        self.lex = Lexicon.load()  # 預設三庫：general-zh / muse-meeting / muse-personal
        self._rebuild_prompts()
        self.warm = False
        self.model_load_s: float | None = None
        log_line(
            f"lexicon loaded: {len(self.lex.replacements)} replacements, "
            f"prompt {len(self.initial_prompt)} chars"
        )

    # ------------------------------------------------------------ model
    def warmup(self) -> None:
        """啟動即載模型：對 1s 靜音跑一次 transcribe，權重進 ModelHolder 常駐。"""
        import mlx_whisper  # 延到這裡 import：載入本身也算 warmup 時間的一部分

        t0 = time.perf_counter()
        mlx_whisper.transcribe(
            np.zeros(SAMPLE_RATE, dtype=np.float32),
            path_or_hf_repo=MODEL,
            language="zh",
            initial_prompt=self.initial_prompt,
        )
        self.model_load_s = time.perf_counter() - t0
        self.warm = True
        log_line(f"model warm: {MODEL} loaded+compiled in {self.model_load_s:.2f}s")

    # ---------------------------------------------------------- handlers
    def handle(self, req: dict) -> dict:
        cmd = req.get("cmd")
        if cmd == "transcribe":
            return self.handle_transcribe(req)
        if cmd == "ping":
            return {"ok": True, "pong": True, "model": MODEL, "warm": self.warm,
                    "version": __version__, "punct_llm": PUNCT_WARMER.status()}
        if cmd == "reload_lexicon":
            self.lex.reload()
            self._rebuild_prompts()
            log_line(
                f"lexicon reloaded: {len(self.lex.replacements)} replacements, "
                f"prompt {len(self.initial_prompt)} chars"
            )
            return {"ok": True, "reloaded": True,
                    "replacements": len(self.lex.replacements),
                    "prompt_chars": len(self.initial_prompt)}
        if cmd == "add_pair":
            return self.handle_add_pair(req)
        if cmd == "stats":
            return self.handle_stats()
        return {"ok": False, "error": "unknown_cmd"}

    def handle_add_pair(self, req: dict) -> dict:
        """UI 教詞庫：寫 muse-personal → 熱重載。"""
        wrong = str(req.get("wrong") or "").strip()
        right = str(req.get("right") or "").strip()
        source = str(req.get("source") or "dictate-ui").strip() or "dictate-ui"
        if not wrong or not right or wrong == right:
            return {"ok": False, "error": "bad_request"}
        try:
            wrote = self.lex.add_pair(wrong, right, source=source)
            # add_pair 內部已 reload；這裡只重算 prompt
            self._rebuild_prompts()
            log_line(f"add_pair: {wrong!r} → {right!r} (source={source}, wrote={wrote})")
            return {"ok": True, "wrong": wrong, "right": right, "wrote": wrote,
                    "replacements": len(self.lex.replacements)}
        except Exception as e:  # noqa: BLE001
            log_line(f"⚠️ add_pair failed: {e!r}")
            return {"ok": False, "error": "add_pair_failed"}

    def handle_stats(self) -> dict:
        """今日 dictation-log 摘要（殼也可本機讀；此 cmd 給 probe / 遠端診斷）。"""
        day = datetime.now().strftime("%Y-%m-%d")
        path = LOG_DIR / f"{day}.jsonl"
        ok = err = hits = 0
        latencies: list[int] = []
        if path.is_file():
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("error"):
                        err += 1
                        continue
                    text = (row.get("text") or "").strip()
                    if text or row.get("total_ms") is not None:
                        ok += 1
                    ch = row.get("changes") or []
                    if ch:
                        hits += 1
                    ms = row.get("total_ms")
                    if isinstance(ms, (int, float)):
                        latencies.append(int(ms))
            except OSError as e:
                log_line(f"⚠️ stats read failed: {e}")
        latencies.sort()

        def pct(p: float) -> int | None:
            if not latencies:
                return None
            i = min(len(latencies) - 1, int((len(latencies) - 1) * p))
            return latencies[i]

        return {
            "ok": True,
            "day": day,
            "count": ok + err,
            "ok_count": ok,
            "error_count": err,
            "lexicon_hits": hits,
            "p50_ms": pct(0.5),
            "p90_ms": pct(0.9),
            "max_ms": latencies[-1] if latencies else None,
            "replacements": len(self.lex.replacements),
        }

    def handle_transcribe(self, req: dict) -> dict:
        import mlx_whisper

        t_total0 = time.perf_counter()
        t_mono0 = time.monotonic()
        wav_path = str(req.get("wav") or "")
        pre_asr_ms = load_ms = asr_ms = lex_ms = punct_ms = 0
        rms: float | None = None
        gate_dr: float | None = None
        gate_voiced: float | None = None
        if not wav_path or not os.path.isfile(wav_path):
            self._log_utterance(None, raw="", text="", changes=[], asr_ms=0,
                                total_ms=self._ms(t_total0), pre_asr_ms=self._ms(t_total0),
                                load_ms=0, lex_ms=0, punct_ms=0,
                                error="file_not_found")
            return {"ok": False, "error": "file_not_found"}

        try:
            # -- 讀音檔（契約格式直解；非契約 fallback ffmpeg）
            t_load0 = time.perf_counter()
            try:
                audio, dur = load_wav(wav_path)
            except (wave.Error, EOFError):
                audio, dur = None, None  # 連 wav header 都不是 → 全權交給 ffmpeg
            finally:
                load_ms = self._ms(t_load0)
            pre_asr_ms = self._ms(t_total0)

            if dur is not None and dur < MIN_DURATION_S:
                self._log_utterance(dur, raw="", text="", changes=[], asr_ms=0,
                                    total_ms=self._ms(t_total0), pre_asr_ms=pre_asr_ms,
                                    load_ms=load_ms, lex_ms=0, punct_ms=0,
                                    error="no_speech")
                return {"ok": False, "error": "no_speech"}

            # -- 能量閘門：數位靜音/死麥直接 no_speech，不進 ASR（見 SILENCE_RMS 註解）
            if audio is not None and audio.size:
                rms = float(np.sqrt(np.square(audio).mean()))
                if rms < SILENCE_RMS:
                    self._log_utterance(dur, raw="", text="", changes=[], asr_ms=0,
                                        total_ms=self._ms(t_total0), pre_asr_ms=pre_asr_ms,
                                        load_ms=load_ms, lex_ms=0, punct_ms=0,
                                        error="no_speech")
                    return {"ok": False, "error": "no_speech"}
                # -- 誤觸閘門（v0.6.0）：短錄音沒有講話的起伏 → 不進 ASR（見 GATE_* 註解）
                gate_hit, gate_dr, gate_voiced = looks_like_no_speech(audio, dur)
                if gate_hit:
                    self._log_utterance(dur, raw="", text="", changes=[], asr_ms=0,
                                        total_ms=self._ms(t_total0), pre_asr_ms=pre_asr_ms,
                                        load_ms=load_ms, lex_ms=0, punct_ms=0,
                                        error="no_speech", dehall="speech_gate",
                                        diag={"rms": round(rms, 5), "dr": round(gate_dr, 1),
                                              "voiced": round(gate_voiced, 2)})
                    return {"ok": False, "error": "no_speech"}

            # -- ASR（keep-warm：ModelHolder 已快取權重；v0.6.0 解碼護欄見 asr_decode_options）
            #    短錄音用「只有詞表」的 prompt：風格句會被當成上文續寫進短句
            #    （重放實測：「你可以購買」→「你可以購買Claude的影片」）。
            src = audio if audio is not None else wav_path
            trim_h = trim_t = 0.0
            adur = dur                          # 實際送進 ASR 的長度（裁過頭尾靜音）
            if audio is not None and audio.size:
                src, trim_h, trim_t = trim_silence(audio)
                adur = len(src) / SAMPLE_RATE
            short = adur is not None and adur < ASR_SHORT_CLIP_S
            prompt0 = getattr(self, "initial_prompt_short", self.initial_prompt) if short else self.initial_prompt
            t_asr0 = time.perf_counter()
            try:
                result = self._asr(mlx_whisper, src, adur, prompt0)
            except Exception as e:
                log_line(f"⚠️ asr_failed for {wav_path}: {e!r}")
                self._log_utterance(dur, raw="", text="", changes=[], asr_ms=self._ms(t_asr0),
                                    total_ms=self._ms(t_total0), pre_asr_ms=pre_asr_ms,
                                    load_ms=load_ms, lex_ms=0, punct_ms=0,
                                    error="asr_failed")
                return {"ok": False, "error": "asr_failed"}
            raw = (result.get("text") or "").strip()
            deloop, deloop_why = self._clean_asr(result)

            # -- 換 prompt 重試（v0.6.0，見 asr_output_anomalous 註解）
            retry_tag = raw_first = None
            if not short and asr_output_anomalous(deloop, adur, gate_voiced):
                deadline = t_mono0 + shell_deadline_s(dur)
                for tag, alt in (("short_prompt", getattr(self, "initial_prompt_short", None)),
                                 ("no_prompt", None)):
                    if deadline - time.monotonic() < (adur or 0) * ASR_RETRY_RTF_BUDGET + 1.0:
                        log_line(f"🔁 ASR 異常但距殼逾時不足，不重試（{dur}s）")
                        break
                    try:
                        r2 = self._asr(mlx_whisper, src, adur, alt)
                    except Exception as e:  # noqa: BLE001 — 重試失敗就用第一次的結果
                        log_line(f"⚠️ asr retry failed: {e!r}")
                        break
                    d2, w2 = self._clean_asr(r2)
                    log_line(f"🔁 ASR 異常（{len(_content_chars(deloop))} 字／{dur}s，{deloop_why}）"
                             f"→ 換 {tag} 重解 → {len(_content_chars(d2))} 字")
                    if not asr_output_anomalous(d2, adur, gate_voiced):
                        raw_first = raw
                        result, raw, deloop, deloop_why, retry_tag = r2, (r2.get("text") or "").strip(), d2, w2, tag
                        break
            asr_ms = self._ms(t_asr0)

            diag = {"rms": round(rms, 5) if rms is not None else None,
                    "dr": round(gate_dr, 1) if gate_dr is not None else None,
                    "voiced": round(gate_voiced, 2) if gate_voiced is not None else None,
                    "asr_temp_max": max((float(sg.get("temperature") or 0.0)
                                         for sg in result.get("segments") or []), default=0.0),
                    "asr_retry": retry_tag, "raw_first": raw_first,
                    "trim_head_s": round(trim_h, 2) or None, "trim_tail_s": round(trim_t, 2) or None}
            if not deloop:
                if deloop_why:
                    log_line(f"🧹 幻覺 → no_speech（{deloop_why}，{dur}s）")
                self._log_utterance(dur, raw=raw, text="", changes=[], asr_ms=asr_ms,
                                    total_ms=self._ms(t_total0), pre_asr_ms=pre_asr_ms,
                                    load_ms=load_ms, lex_ms=0, punct_ms=0,
                                    error="no_speech", dehall=deloop_why, diag=diag)
                return {"ok": False, "error": "no_speech"}

            # -- 確定性校正（詞庫命中才改，絕無 LLM，見契約§校正哲學）
            t_lex0 = time.perf_counter()
            text, changes = self.lex.correct(deloop)
            lex_ms = self._ms(t_lex0)

            # -- 標點層（v0.4 三模式；raw 欄位永遠是原始輸出）
            #    smart_zh＝規則層；llm_zh＝LLM 標點+受控語境錯字（閘門 v2 不過自動退回）；raw＝原樣
            punct_mode = str(req.get("punct") or "smart_zh")
            t_punct0 = time.perf_counter()
            if punct_mode == "llm_zh":
                contextual = list(getattr(self.lex, "contextual", []) or [])
                text, punct_mode = llm_punct_routed(text, contextual, self._safe_pairs,
                                                    deadline=t_mono0 + shell_deadline_s(dur))
            elif punct_mode == "smart_zh":
                text = smart_punct_zh(text)
            punct_ms = self._ms(t_punct0)

            total_ms = self._ms(t_total0)
            self._log_utterance(dur, raw=raw, text=text, changes=changes,
                                asr_ms=asr_ms, total_ms=total_ms, pre_asr_ms=pre_asr_ms,
                                load_ms=load_ms, lex_ms=lex_ms, punct_ms=punct_ms,
                                punct=punct_mode,
                                dehall=deloop_why, diag=diag)
            return {"ok": True, "text": text, "raw": raw, "changes": changes,
                    "punct": punct_mode, "asr_ms": asr_ms, "total_ms": total_ms,
                    "pre_asr_ms": pre_asr_ms, "load_ms": load_ms,
                    "lex_ms": lex_ms, "punct_ms": punct_ms}
        finally:
            cleanup_wav(wav_path)  # 成功/no_speech/失敗都清（殼不重送）

    @staticmethod
    def _asr(mlx_whisper, src, dur, prompt) -> dict:
        extra = asr_decode_options(dur)
        if not isinstance(src, str):
            clips = asr_clip_timestamps(src)
            if clips:
                extra["clip_timestamps"] = clips
        return mlx_whisper.transcribe(src, path_or_hf_repo=MODEL, language="zh",
                                      initial_prompt=prompt, **extra)

    @staticmethod
    def _clean_asr(result: dict) -> tuple[str, str | None]:
        """ASR 結果 → (可用文字, 說明)。文字為空＝no_speech。

        順序是正確性條件：no_speech 判定 → 小寫形標點 → 已知署名 → 迴圈尾巴（必須在 lex.correct 之前）。
        """
        if is_no_speech(result):
            return "", None
        raw = normalize_small_forms((result.get("text") or "").strip())
        dehall_raw, known_why = strip_known_hallucination(raw)
        if not dehall_raw:
            return "", known_why
        deloop, deloop_why = strip_tail_hallucination(dehall_raw)
        if known_why:
            deloop_why = f"{known_why}; {deloop_why}" if deloop_why else known_why
        return deloop, deloop_why

    # ------------------------------------------------------------- utils
    @property
    def _safe_pairs(self) -> list[tuple[str, str]]:
        """replacements 中的純字串 pair（無 regex 元字元、非 lambda）→ 閘門 v2 白名單的安全側。"""
        out = []
        for pattern, repl, _desc in self.lex.replacements:
            if callable(repl):
                continue
            if re.escape(pattern) != pattern:
                continue
            out.append((pattern, str(repl)))
        return out

    def _rebuild_prompts(self) -> None:
        self.initial_prompt = build_whisper_prompt(self.lex)
        self.initial_prompt_short = build_whisper_prompt(self.lex, with_seed=False)

    @staticmethod
    def _ms(t0: float) -> int:
        return int(round((time.perf_counter() - t0) * 1000))

    def _log_utterance(self, wav_dur_s, *, raw, text, changes, asr_ms, total_ms,
                       pre_asr_ms=None, load_ms=None, lex_ms=None, punct_ms=None,
                       error=None, punct=None, dehall=None, diag=None):
        """每句 → ~/.open-dictate/dictation-log/YYYY-MM-DD.jsonl（本機私有，絕不進 git）。"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "wav_dur_s": round(wav_dur_s, 2) if wav_dur_s is not None else None,
                "raw": raw,
                "text": text,
                "changes": changes,
                "pre_asr_ms": pre_asr_ms,
                "load_ms": load_ms,
                "asr_ms": asr_ms,
                "lex_ms": lex_ms,
                "punct_ms": punct_ms,
                "total_ms": total_ms,
            }
            if punct:
                entry["punct"] = punct
            if error:
                entry["error"] = error
            if dehall:
                entry["dehall"] = dehall   # 尾巴層動過手 → 留痕，供回歸掃描對帳
            if diag:
                # v0.6.0 診斷欄位：rms（誤觸門檻日後用真資料調）、asr_temp_max（>0 ＝ 溫度 fallback 有觸發）
                entry.update({k: v for k, v in diag.items() if v is not None})
            day = datetime.now().strftime("%Y-%m-%d")
            with open(LOG_DIR / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            log_line(f"⚠️ log write failed: {e}")


# ---------------------------------------------------------------------------
# Socket server（單執行緒序列處理：dictation 一次一句）
# ---------------------------------------------------------------------------
def _recv_line(conn: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break  # client 半關：EOF 也視為一則結束
        buf += chunk
        if len(buf) > RECV_LIMIT:
            raise ValueError("request too large")
    return buf.split(b"\n", 1)[0]


def _assert_not_running(path: str) -> None:
    """socket 檔存在時：活 daemon → 讓位退出；殭屍檔 → 清掉。"""
    if not os.path.exists(path):
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(path)
        probe.close()
        log_line(f"❌ another daemon is alive on {path}, exiting")
        sys.exit(1)
    except (ConnectionRefusedError, socket.timeout, OSError):
        os.unlink(path)
        log_line(f"stale socket removed: {path}")
    finally:
        probe.close()


def serve(daemon: DictationDaemon, socket_path: str) -> None:
    _assert_not_running(socket_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)  # socket file → 0600（僅本人可連）
    try:
        server.bind(socket_path)
    finally:
        os.umask(old_umask)
    os.chmod(socket_path, 0o600)
    server.listen(8)

    def shutdown(signum, _frame):
        log_line(f"signal {signum} → shutdown")
        try:
            server.close()
        finally:
            if os.path.exists(socket_path):
                os.unlink(socket_path)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log_line(f"listening on {socket_path} (pid {os.getpid()})")

    # accept loop：例外不死 —— 單一請求爆掉回 error json，daemon 活著
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            continue  # server closed during shutdown race
        with conn:
            conn.settimeout(SOCKET_TIMEOUT_S)
            try:
                line = _recv_line(conn)
                if not line.strip():
                    continue
                try:
                    req = json.loads(line.decode("utf-8"))
                    if not isinstance(req, dict):
                        raise ValueError("request must be a JSON object")
                except (ValueError, UnicodeDecodeError):
                    resp = {"ok": False, "error": "bad_request"}
                else:
                    resp = daemon.handle(req)
            except Exception as e:  # noqa: BLE001 — daemon 不死鐵律
                log_line(f"⚠️ request handling error: {e!r}")
                resp = {"ok": False, "error": "asr_failed"}
            try:
                conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            except OSError as e:
                log_line(f"⚠️ send failed (client gone?): {e}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=f"open-dictate daemon v{__version__}")
    parser.add_argument("--socket", default=SOCKET_PATH,
                        help=f"unix socket path（預設契約路徑 {SOCKET_PATH}）")
    args = parser.parse_args()

    log_line(f"dictated v{__version__} starting (model={MODEL})")
    daemon = DictationDaemon()
    daemon.warmup()  # 先 warm 再開 socket：殼連上即可用，不會撞冷啟動
    PUNCT_WARMER.start()  # v0.6.0：LLM 標點模型在背景載入＋常駐看守，不擋 socket
    serve(daemon, args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
