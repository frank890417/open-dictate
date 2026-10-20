"""ASR engine: model table + decoding, shared by the local worker and the remote server.

The local recognition subprocess (asr_worker.py) and the remote recognition service
(asr_server.py, e.g. on a desktop Mac on the same tailnet) both import this module, so
"fall back to local" always means *the same model with the same decoding parameters*.

Depends only on numpy plus the model libraries (mlx_whisper / mlx_audio), imported lazily.
No glossary, no punctuation, no logging of transcripts: the daemon owns all of that.
"""

from __future__ import annotations

import io
import math
import os
import time
import wave

import numpy as np

ENGINE_VERSION = "1.1.0"
SAMPLE_RATE = 16000
_FRAME = 480                 # 30 ms @16k

# key → model spec. ``native_punct``: the model writes its own punctuation (the daemon
# then skips the LLM punctuation layer). Unknown names are treated as a Hugging Face repo
# id (see ``spec``), so existing ``MODEL=mlx-community/...`` configs keep working.
MODELS: dict[str, dict] = {
    "whisper-turbo": {"repo": "mlx-community/whisper-large-v3-turbo", "kind": "whisper", "native_punct": False},
    "whisper-v3": {"repo": "mlx-community/whisper-large-v3-mlx", "kind": "whisper", "native_punct": False},
    "qwen3-asr-1.7b": {"repo": "mlx-community/Qwen3-ASR-1.7B-8bit", "kind": "qwen3_asr", "native_punct": True},
    "qwen3-asr-0.6b": {"repo": "mlx-community/Qwen3-ASR-0.6B-8bit", "kind": "qwen3_asr", "native_punct": True},
}


def resolve(name: str) -> str:
    """Model key for a key or a repo id ("" stays "")."""
    name = (name or "").strip()
    for key, s in MODELS.items():
        if name in (key, s["repo"]):
            return key
    return name


def spec(name: str) -> dict:
    key = resolve(name)
    if key in MODELS:
        return {"key": key, **MODELS[key]}
    kind = "qwen3_asr" if "qwen3-asr" in key.lower() else "whisper"
    return {"key": key, "repo": key, "kind": kind, "native_punct": kind == "qwen3_asr"}


# ---------------------------------------------------------------------------
# Whisper decoding guards (moved here from dictated.py v0.6.0; dictated re-exports them)
#
# ① sample_len follows the clip length: real dictation p99 is ~7 text tokens/s, we allow
#   12/s + 32, so a 1.5 s clip gets at most 50 tokens; ≥16 s equals the old 224.
# ② temperature ladder stops at 0.4: in replay, decodes that reached 1.0 were garbage
#   12 times out of 15. Clips < 4 s keep only two temperatures.
# ---------------------------------------------------------------------------
ASR_TOKENS_PER_S = 12.0
ASR_TOKENS_BASE = 32
ASR_SAMPLE_LEN_MAX = 224
ASR_SHORT_CLIP_S = 4.0
ASR_TEMPS = (0.0, 0.2, 0.4)
ASR_SHORT_TEMPS = (0.0, 0.4)


def asr_decode_options(dur: float | None) -> dict:
    """Extra mlx_whisper.transcribe options (sample_len / temperature) for a clip length."""
    if not dur or dur <= 0:
        return {}
    opts: dict = {"sample_len": min(ASR_SAMPLE_LEN_MAX,
                                    int(math.ceil(dur * ASR_TOKENS_PER_S)) + ASR_TOKENS_BASE)}
    opts["temperature"] = ASR_SHORT_TEMPS if dur < ASR_SHORT_CLIP_S else ASR_TEMPS
    return opts


# 30–33 s clips: Whisper cuts a window every 30 s and the leftover 0.26 s sliver gets
# continued into a loop. Only in that band, cut once at the quietest 0.3 s within ±3 s
# of the middle and hand it to mlx_whisper as clip_timestamps (see dictated.py history).
CLIP_SLIVER_MIN_S = 30.0
CLIP_SLIVER_MAX_S = 33.0
CLIP_SEARCH_S = 3.0


def asr_clip_timestamps(audio) -> list[float] | None:
    dur = len(audio) / SAMPLE_RATE
    if not (CLIP_SLIVER_MIN_S < dur < CLIP_SLIVER_MAX_S):
        return None
    n = len(audio) // _FRAME
    r = np.sqrt(np.square(audio[: n * _FRAME].reshape(n, _FRAME)).mean(axis=1))
    fs = _FRAME / SAMPLE_RATE
    lo = int((dur / 2 - CLIP_SEARCH_S) / fs)
    hi = int((dur / 2 + CLIP_SEARCH_S) / fs)
    win = max(1, int(0.3 / fs))
    seg = np.convolve(r[lo:hi], np.ones(win) / win, mode="same")
    cut = round((lo + int(np.argmin(seg))) * fs, 2)
    return [0.0, cut, cut, round(dur, 2)]


# Qwen3-ASR: cap generated tokens by clip length (Chinese ≈ 1 token per character,
# fast speech ≈ 7 characters/s). The library default is 8192 — a loop would run for minutes.
QWEN_TOKENS_PER_S = 15.0
QWEN_TOKENS_BASE = 64
QWEN_LANGUAGE = os.environ.get("OPEN_DICTATE_QWEN_LANGUAGE", "Chinese")


def qwen_max_tokens(dur: float | None) -> int:
    return min(8192, int(math.ceil((dur or 30.0) * QWEN_TOKENS_PER_S)) + QWEN_TOKENS_BASE)


# ---------------------------------------------------------------------------
# Audio helpers (both directions; FLAC needs soundfile, WAV is stdlib)
# ---------------------------------------------------------------------------
def read_wav(path: str) -> np.ndarray:
    """Contract WAV (16 kHz PCM16, mono or multi-channel) → float32 mono."""
    with wave.open(path, "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getsampwidth() != 2:
            raise ValueError("not a 16 kHz PCM16 wav")
        ch = w.getnchannels()
        data = w.readframes(w.getnframes())
    audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    return audio.reshape(-1, ch).mean(axis=1) if ch > 1 else audio


def encode_audio(audio: np.ndarray) -> tuple[bytes, str]:
    """float32 mono 16 kHz → (bytes, content type). FLAC when soundfile is available, else WAV."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).round().astype(np.int16)
    try:
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, pcm, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
        return buf.getvalue(), "audio/flac"
    except ImportError:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        return buf.getvalue(), "audio/wav"


def decode_audio(data: bytes, content_type: str) -> np.ndarray:
    """Request body → float32 mono 16 kHz. Rejects other sample rates (the client resamples)."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in ("audio/flac", "audio/x-flac"):
        import soundfile as sf
        audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
        if sr != SAMPLE_RATE:
            raise ValueError(f"sample rate {sr} != {SAMPLE_RATE}")
        return audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
    if ctype in ("audio/wav", "audio/x-wav", "audio/wave"):
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getframerate() != SAMPLE_RATE or w.getsampwidth() != 2:
                raise ValueError("not a 16 kHz PCM16 wav")
            ch = w.getnchannels()
            pcm = w.readframes(w.getnframes())
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return audio.reshape(-1, ch).mean(axis=1) if ch > 1 else audio
    raise ValueError(f"unsupported content type {content_type!r}")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class Engine:
    """Loads models once and transcribes. Not thread-safe: call from one thread."""

    def __init__(self) -> None:
        self._qwen: dict[str, object] = {}
        self.loaded: dict[str, float] = {}       # key → load+warmup seconds
        limit_mb = os.environ.get("OPEN_DICTATE_MLX_CACHE_LIMIT_MB", "2048")
        try:
            import mlx.core as mx
            # long-running process: keep MLX's buffer cache from growing without bound
            mx.set_cache_limit(int(limit_mb) << 20)
        except Exception:  # noqa: BLE001 — no MLX (tests) or old MLX: nothing to cap
            pass

    def load(self, name: str) -> float:
        s = spec(name)
        t0 = time.perf_counter()
        warm = (np.random.default_rng(0).standard_normal(SAMPLE_RATE) * 1e-3).astype(np.float32)
        if s["kind"] == "whisper":
            import mlx_whisper
            mlx_whisper.transcribe(warm, path_or_hf_repo=s["repo"], language="zh")
        else:
            from mlx_audio.stt.utils import load
            model = load(s["repo"])
            model.generate(warm, language=QWEN_LANGUAGE, max_tokens=8)
            self._qwen[s["key"]] = model
        self.loaded[s["key"]] = round(time.perf_counter() - t0, 2)
        return self.loaded[s["key"]]

    def transcribe(self, name: str, audio, prompt: str | None = None,
                   hotwords: list[str] | None = None) -> dict:
        """audio: float32 mono 16 kHz array (or a file path, decoded by the library).

        ``prompt`` is Whisper's initial_prompt; ``hotwords`` is Qwen3-ASR's term list
        (folded into its system prompt by mlx_audio). Each model ignores the other one.

        Returns {"text", "segments": [{"text", "no_speech_prob", "temperature"}], "engine_ms", "model"}.
        Whisper segments come from the decoder; Qwen3-ASR has no segments or no-speech
        probability, so it reports one segment (or none when it heard nothing).
        """
        s = spec(name)
        dur = (len(audio) / SAMPLE_RATE) if not isinstance(audio, str) else None
        t0 = time.perf_counter()
        if s["kind"] == "whisper":
            import mlx_whisper
            extra = asr_decode_options(dur)
            if not isinstance(audio, str):
                clips = asr_clip_timestamps(audio)
                if clips:
                    extra["clip_timestamps"] = clips
            r = mlx_whisper.transcribe(audio, path_or_hf_repo=s["repo"], language="zh",
                                       initial_prompt=prompt or None, **extra)
            text = r.get("text") or ""
            segments = [{"text": sg.get("text") or "",
                         "no_speech_prob": float(sg.get("no_speech_prob") or 0.0),
                         "temperature": float(sg.get("temperature") or 0.0)}
                        for sg in r.get("segments") or []]
        else:
            model = self._qwen.get(s["key"])
            if model is None:
                self.load(s["key"])
                model = self._qwen[s["key"]]
            out = model.generate(audio, language=QWEN_LANGUAGE, max_tokens=qwen_max_tokens(dur),
                                 hotwords=list(hotwords) if hotwords else None)
            text = (getattr(out, "text", "") or "").strip()
            segments = [{"text": text, "no_speech_prob": 0.0, "temperature": 0.0}] if text else []
        return {"text": text, "segments": segments, "model": s["key"],
                "engine_ms": int(round((time.perf_counter() - t0) * 1000))}
