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
    from .product_config import LOG_ROOT, LEXICON_ROOT, PRIORITY_TERMS, SOCKET_PATH, env
except ImportError:  # direct script execution: python daemon/dictated.py
    from product_config import LOG_ROOT, LEXICON_ROOT, PRIORITY_TERMS, SOCKET_PATH, env

__version__ = "0.5.3"

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

# muse_lexicon: import from the selected lexicon root
sys.path.insert(0, str(LEXICON_ROOT / "tools" / "muse-lexicon"))
from muse_lexicon import Lexicon, apply_opencc, smart_punct_zh  # noqa: E402

# initial_prompt 風格種子：全形標點＋繁體範例句，讓 whisper 從解碼端就傾向
# 全形/繁體輸出（smart_punct_zh 後處理是第二道保險）。混中英：種子含英文專名示範。
PUNCT_STYLE_SEED = "好的，我知道了。我們用 Claude 跟 TouchDesigner 來做，這樣就對了！"

# 優先專名（prompt 尾端 — whisper 對超長 prompt 只保留尾部 piece，放尾端最保險）。
# 校準實證：專名不進解碼端就會不斷變形，而且同一個詞每次錯法還不一樣。
# 詞庫 _canonical 多半是人名，領域詞彙由 product config 的 PRIORITY_TERMS 補上。
DICTATE_PRIORITY_TERMS = PRIORITY_TERMS

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

# 逾時預算：長度感知（v0.5.3）
#
# 舊版是固定 8.0s。這個工作要 LLM 重寫整段，decode 成本隨文長線性成長，
# 固定預算配變動成本 ⇒ 長句結構上不可能做完，而代價不是「標點差一點」，
# 是先白等一輪逾時、再拿到比較差的標點。
#
# ⚠️ 誠實標：長度**不是**逾時的唯一成因。把生產環境曾逾時的樣本原樣重跑，
# 二十字的句子只要約 0.3 秒、一個字的只要約 0.1 秒——短句根本不缺時間，
# 它們是被整機資源競爭卡住的。所以這條改的是「長句本來就不該用短句的預算」，
# 不宣稱它能解決全部逾時；真正的補救是下面 except 分支把現場證據印出來。
PUNCT_LLM_TIMEOUT_BASE_S = float(env("PUNCT_BASE_S", "4.0"))
PUNCT_LLM_TIMEOUT_PER_CHAR_S = float(env("PUNCT_PER_CHAR_S", "0.025"))
# ⚠️ CAP 跟殼的逾時是一組契約，不可以只改一邊 ⚠️
# 殼 AppDelegate.swift `transcribeTimeout()` = max(15, 音檔秒/6 + llmHeadroom)，
# llmHeadroom 現值 12 秒。CAP 開超過 10 會讓長口述出現
# 「殼判定 daemon 離線、daemon 其實在背景跑完」的假離線。要往上調，兩邊一起調。
PUNCT_LLM_TIMEOUT_CAP_S = float(env("PUNCT_CAP_S", "10.0"))


def punct_timeout_for(n_chars: int) -> float:
    """長度感知逾時預算（秒）。40 字→5.0s／150 字→7.8s／240 字以上→cap。"""
    return min(PUNCT_LLM_TIMEOUT_BASE_S + n_chars * PUNCT_LLM_TIMEOUT_PER_CHAR_S,
               PUNCT_LLM_TIMEOUT_CAP_S)


def _machine_load() -> str:
    """逾時當下的整機負載快照。純唯讀、失敗不影響主流程。

    存在的理由：逾時最常見的成因是整機資源競爭，而事後很難重建當時的機器狀態。
    把 load 與記憶體壓力印在逾時那一行，下一個讀 log 的人就不必靠推理。
    """
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
                      safe_pairs: list[tuple[str, str]]) -> str | None:
    """LLM 標點修復 + 受控語境錯字修正（punct="llm_zh"，daemon v0.4）。

    project rule：「頂多做格式跟錯字校正，其他都不要動。」保證不靠信任靠閘門：
    輸出經 opencc 正規化後，其 content 序列必須可由輸入 content「僅套用授權 pair」重建
    （授權 = 詞庫 _contextual + 安全 replacements 的純字串 pair）。任何表外變動 → 丟棄 fallback。
    """
    if not text or len(text) > PUNCT_LLM_MAX_CHARS:
        return None
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
    budget_s = punct_timeout_for(len(text))
    try:
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=budget_s) as r:
            out = (json.load(r).get("message") or {}).get("content", "").strip()
        ms = int((time.perf_counter() - t0) * 1000)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
        # 逾時當場留證據：字數/預算/實耗分辨「預算不夠」vs「被卡住」，
        # 機器負載分辨「模型慢」vs「整機忙」。少了這些就只能事後推理。
        spent = int((time.perf_counter() - t0) * 1000)
        log_line(f"llm_punct 不可用（{e.__class__.__name__}，{len(text)} 字／"
                 f"預算 {budget_s:.1f}s／實耗 {spent}ms／{_machine_load()}）→ fallback 規則層")
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
    log_line(f"llm_punct ok（{ms}ms）")
    return out

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


def strip_tail_hallucination(text: str) -> tuple[str, str | None]:
    """砍掉貼著結尾的迴圈幻覺。回傳 (文字, 說明)；文字為空 = 呼叫端應判 no_speech。"""
    s = (text or "").rstrip()
    m = TAIL_LOOP.search(s)
    if not m:
        return text, None
    tok = m.group(1)
    if not tok.strip() or not _WORDISH.search(tok):
        return text, None                       # 純標點/空白的重複不碰
    reps = len(re.findall(re.escape(tok), m.group()))
    body = s[: m.start()].strip()
    if len(body) >= TAIL_MIN_BODY:
        if reps >= TAIL_REPS_WITH_BODY:
            return body, f"tail_loop {tok!r}x{reps} ({len(m.group())} chars)"
        return text, None
    if reps >= TAIL_REPS_SHORT_BODY or len(m.group()) >= TAIL_SHORT_BODY_MIN_SPAN:
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
        self.initial_prompt = self._build_prompt()
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
                    "version": __version__}
        if cmd == "reload_lexicon":
            self.lex.reload()
            self.initial_prompt = self._build_prompt()
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
            self.initial_prompt = self._build_prompt()
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
        wav_path = str(req.get("wav") or "")
        if not wav_path or not os.path.isfile(wav_path):
            return {"ok": False, "error": "file_not_found"}

        try:
            # -- 讀音檔（契約格式直解；非契約 fallback ffmpeg）
            try:
                audio, dur = load_wav(wav_path)
            except (wave.Error, EOFError):
                audio, dur = None, None  # 連 wav header 都不是 → 全權交給 ffmpeg

            if dur is not None and dur < MIN_DURATION_S:
                self._log_utterance(dur, raw="", text="", changes=[], asr_ms=0,
                                    total_ms=self._ms(t_total0), error="no_speech")
                return {"ok": False, "error": "no_speech"}

            # -- 能量閘門：數位靜音/死麥直接 no_speech，不進 ASR（見 SILENCE_RMS 註解）
            if audio is not None and audio.size:
                rms = float(np.sqrt(np.square(audio).mean()))
                if rms < SILENCE_RMS:
                    self._log_utterance(dur, raw="", text="", changes=[], asr_ms=0,
                                        total_ms=self._ms(t_total0), error="no_speech")
                    return {"ok": False, "error": "no_speech"}

            # -- ASR（keep-warm：ModelHolder 已快取權重）
            t_asr0 = time.perf_counter()
            try:
                result = mlx_whisper.transcribe(
                    audio if audio is not None else wav_path,
                    path_or_hf_repo=MODEL,
                    language="zh",
                    initial_prompt=self.initial_prompt,
                )
            except Exception as e:
                log_line(f"⚠️ asr_failed for {wav_path}: {e!r}")
                self._log_utterance(dur, raw="", text="", changes=[], asr_ms=self._ms(t_asr0),
                                    total_ms=self._ms(t_total0), error="asr_failed")
                return {"ok": False, "error": "asr_failed"}
            asr_ms = self._ms(t_asr0)

            raw = (result.get("text") or "").strip()
            if is_no_speech(result):
                self._log_utterance(dur, raw=raw, text="", changes=[], asr_ms=asr_ms,
                                    total_ms=self._ms(t_total0), error="no_speech")
                return {"ok": False, "error": "no_speech"}

            # -- 迴圈幻覺尾巴層（v0.5.3）：必須在 lex.correct() 之前，見該函式註解
            deloop, deloop_why = strip_tail_hallucination(raw)
            if not deloop:
                log_line(f"🔁 整句迴圈幻覺 → no_speech（{deloop_why}）")
                self._log_utterance(dur, raw=raw, text="", changes=[], asr_ms=asr_ms,
                                    total_ms=self._ms(t_total0), error="no_speech",
                                    dehall=deloop_why)
                return {"ok": False, "error": "no_speech"}

            # -- 確定性校正（詞庫命中才改，絕無 LLM，見契約§校正哲學）
            text, changes = self.lex.correct(deloop)

            # -- 標點層（v0.4 三模式；raw 欄位永遠是原始輸出）
            #    smart_zh＝規則層；llm_zh＝LLM 標點+受控語境錯字（閘門 v2 不過自動退回）；raw＝原樣
            punct_mode = str(req.get("punct") or "smart_zh")
            if punct_mode == "llm_zh":
                contextual = list(getattr(self.lex, "contextual", []) or [])
                fixed = llm_punct_and_fix(text, contextual, self._safe_pairs)
                if fixed is not None:
                    text = fixed
                else:
                    text = smart_punct_zh(text)
                    punct_mode = "smart_zh_fallback"
            elif punct_mode == "smart_zh":
                text = smart_punct_zh(text)

            total_ms = self._ms(t_total0)
            self._log_utterance(dur, raw=raw, text=text, changes=changes,
                                asr_ms=asr_ms, total_ms=total_ms, punct=punct_mode,
                                dehall=deloop_why)
            return {"ok": True, "text": text, "raw": raw, "changes": changes,
                    "punct": punct_mode, "asr_ms": asr_ms, "total_ms": total_ms}
        finally:
            cleanup_wav(wav_path)  # 成功/no_speech/失敗都清（殼不重送）

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

    def _build_prompt(self) -> str:
        """風格種子 + 詞庫專名串（150 字）+ 優先生態系詞（尾端＝whisper 截斷時保留）。"""
        return PUNCT_STYLE_SEED + self.lex.build_initial_prompt(max_chars=150) + "、" + DICTATE_PRIORITY_TERMS

    @staticmethod
    def _ms(t0: float) -> int:
        return int(round((time.perf_counter() - t0) * 1000))

    def _log_utterance(self, wav_dur_s, *, raw, text, changes, asr_ms, total_ms,
                       error=None, punct=None, dehall=None):
        """每句 → ~/.open-dictate/dictation-log/YYYY-MM-DD.jsonl（本機私有，絕不進 git）。"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "wav_dur_s": round(wav_dur_s, 2) if wav_dur_s is not None else None,
                "raw": raw,
                "text": text,
                "changes": changes,
                "asr_ms": asr_ms,
                "total_ms": total_ms,
            }
            if punct:
                entry["punct"] = punct
            if error:
                entry["error"] = error
            if dehall:
                entry["dehall"] = dehall   # 尾巴層動過手 → 留痕，供回歸掃描對帳
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
    serve(daemon, args.socket)
    return 0


if __name__ == "__main__":
    sys.exit(main())
