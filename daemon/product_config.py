"""Public-safe product/runtime configuration shared by daemon entry points.

Open Dictate remains the default. A downstream distribution selects its own
namespace with ``OPEN_DICTATE_PRODUCT_ENV_PREFIX`` and then supplies values
through that prefix, for example ``MUSE_DICTATE_SOCKET_PATH``.
"""
from __future__ import annotations

import os
from pathlib import Path


ENV_PREFIX = os.environ.get("OPEN_DICTATE_PRODUCT_ENV_PREFIX", "OPEN_DICTATE").strip() or "OPEN_DICTATE"


def env(name: str, default: str) -> str:
    """Read the selected product prefix, preserving OPEN_DICTATE compatibility."""
    return os.environ.get(f"{ENV_PREFIX}_{name}", os.environ.get(f"OPEN_DICTATE_{name}", default))


PRODUCT_ID = env("PRODUCT_ID", "open-dictate")
APP_NAME = env("APP_NAME", "OpenDictate")
SOCKET_PATH = env("SOCKET_PATH", "/tmp/open-dictate.sock")
DATA_ROOT = Path(env("DATA_ROOT", "~/.open-dictate")).expanduser()
LOG_ROOT = Path(env("LOG_ROOT", str(DATA_ROOT / "dictation-log"))).expanduser()
DAEMON_LAUNCH_LABEL = env("DAEMON_LABEL", "org.opendictate.daemon")
SHELL_LAUNCH_LABEL = env("SHELL_LABEL", "org.opendictate.shell")
DEFAULT_LEXICON_ROOT = Path(__file__).resolve().parents[1] / "vendor"
LEXICON_ROOT = Path(env("LEXICON_ROOT", str(DEFAULT_LEXICON_ROOT))).expanduser()
PRIORITY_TERMS = env(
    "PRIORITY_TERMS",
    "Open Dictate、OpenDictate、TouchDesigner、p5.js、Obsidian、Notion、"
    "Hahow、Hermes、Ollama、launchd、Python、Swift、macOS、Apple Silicon、"
    "繁體中文、全形標點、詞庫、語音輸入、開源專案",
)

