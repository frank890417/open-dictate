#!/usr/bin/env bash
# Install any validated Open Dictate product flavor and its isolated launchd jobs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG=""
DRY_RUN=0
RENDER_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="${2:?missing config path}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --render-dir) RENDER_DIR="${2:?missing render dir}"; shift 2 ;;
    -h|--help) echo "usage: $0 --config <json> [--dry-run | --render-dir <dir>]"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$CONFIG" ]] || { echo "--config is required" >&2; exit 64; }
CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
eval "$(python3 "$ROOT/scripts/product-config-env.py" --config "$CONFIG" --require-lexicon)"

expand_path() { python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$1"; }
DATA_ROOT="$(expand_path "$PRODUCT_DATA_ROOT")"
LOG_ROOT="$(expand_path "$PRODUCT_LOG_ROOT")"
RUNTIME_LOG_ROOT="$(expand_path "$PRODUCT_RUNTIME_LOG_ROOT")"
LEXICON_ROOT="$(expand_path "$PRODUCT_LEXICON_ROOT")"
APP_PATH="/Applications/$PRODUCT_APP_NAME.app"
PREFIX="$PRODUCT_ENV_PREFIX"

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf 'product=%s\napp=%s\nsocket=%s\ndata=%s\nlog=%s\nruntime_log=%s\ndaemon=%s\nshell=%s\nlexicon=%s\nmodel=%s\nsigning=%s\n' \
    "$PRODUCT_APP_NAME" "$APP_PATH" "$PRODUCT_SOCKET_PATH" "$DATA_ROOT" "$LOG_ROOT" "$RUNTIME_LOG_ROOT" \
    "$PRODUCT_DAEMON_LABEL" "$PRODUCT_SHELL_LABEL" "$LEXICON_ROOT" \
    "${PRODUCT_MODEL:-<daemon default>}" "${PRODUCT_SIGNING_IDENTITY:-<default>}"
  exit 0
fi

if [[ -z "$RENDER_DIR" ]]; then
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo "Apple Silicon macOS required" >&2; exit 69; }
[[ -f "$LEXICON_ROOT/tools/muse-lexicon/muse_lexicon.py" ]] || { echo "invalid lexicon root: $LEXICON_ROOT" >&2; exit 66; }
if [[ "$PRODUCT_LEXICON_PROVIDER" == external ]]; then
  PYTHON="$LEXICON_ROOT/tools/td-subtitle/.venv/bin/python3"
  [[ -x "$PYTHON" ]] || { echo "external Python runtime missing: $PYTHON" >&2; exit 66; }
else
  PYTHON="$ROOT/.venv-dictate/bin/python3"
  if [[ ! -x "$PYTHON" ]]; then python3 -m venv "$ROOT/.venv-dictate"; fi
  "$PYTHON" -m pip install -r "$ROOT/daemon/requirements.txt"
fi
"$PYTHON" -c 'import mlx_whisper, opencc'

"$ROOT/scripts/build-product.sh" --config "$CONFIG" --output "$ROOT/dist-product"
rm -rf "$APP_PATH"
ditto "$ROOT/dist-product/$PRODUCT_APP_NAME.app" "$APP_PATH"
codesign --verify --deep "$APP_PATH"
mkdir -p "$RUNTIME_LOG_ROOT" "$LOG_ROOT" "$HOME/Library/LaunchAgents"
PLIST_DIR="$HOME/Library/LaunchAgents"
else
  # --render-dir: write the plists for inspection only. No build, no copy, no launchctl.
  if [[ "$PRODUCT_LEXICON_PROVIDER" == external ]]; then
    PYTHON="$LEXICON_ROOT/tools/td-subtitle/.venv/bin/python3"
  else
    PYTHON="$ROOT/.venv-dictate/bin/python3"
  fi
  PLIST_DIR="$(cd "$RENDER_DIR" && pwd)"
fi

DAEMON_PLIST="$PLIST_DIR/$PRODUCT_DAEMON_LABEL.plist"
SHELL_PLIST="$PLIST_DIR/$PRODUCT_SHELL_LABEL.plist"
PRODUCT_RUNTIME_LOG_ROOT="$RUNTIME_LOG_ROOT" python3 - "$DAEMON_PLIST" "$SHELL_PLIST" "$PYTHON" "$ROOT" "$APP_PATH" "$PREFIX" "$LEXICON_ROOT" <<'PY'
import os, plistlib, sys
daemon_path, shell_path, python, root, app, prefix, lexicon = sys.argv[1:]
def e(suffix, value): return {f"{prefix}_{suffix}": value}
values = {
    "APP_NAME": os.environ["PRODUCT_APP_NAME"], "PRODUCT_ID": os.environ["PRODUCT_ID"],
    "SOCKET_PATH": os.environ["PRODUCT_SOCKET_PATH"], "DATA_ROOT": os.path.expanduser(os.environ["PRODUCT_DATA_ROOT"]),
    "LOG_ROOT": os.path.expanduser(os.environ["PRODUCT_LOG_ROOT"]), "LEXICON_ROOT": lexicon,
    "DAEMON_LABEL": os.environ["PRODUCT_DAEMON_LABEL"], "SHELL_LABEL": os.environ["PRODUCT_SHELL_LABEL"],
    "PRIORITY_TERMS": os.environ["PRODUCT_PRIORITY_TERMS"],
}
# Optional runtime settings: only exported when the product config sets them,
# so an unset field leaves the daemon on its built-in default.
if os.environ.get("PRODUCT_MODEL"): values["MODEL"] = os.environ["PRODUCT_MODEL"]
if os.environ.get("PRODUCT_PROMPT_CORE_TERMS"): values["PROMPT_CORE_TERMS"] = os.environ["PRODUCT_PROMPT_CORE_TERMS"]
env = {"OPEN_DICTATE_PRODUCT_ENV_PREFIX": prefix}
for key, value in values.items(): env.update(e(key, value))
runtime_logs = os.path.abspath(os.path.expanduser(os.environ["PRODUCT_RUNTIME_LOG_ROOT"]))
daemon = {"Label": os.environ["PRODUCT_DAEMON_LABEL"], "ProgramArguments": [python, os.path.join(root, "daemon", "dictated.py")],
          "EnvironmentVariables": env, "RunAtLoad": True, "KeepAlive": True, "ProcessType": "Interactive",
          "StandardOutPath": os.path.join(runtime_logs, "daemon.out.log"), "StandardErrorPath": os.path.join(runtime_logs, "daemon.err.log")}
shell = {"Label": os.environ["PRODUCT_SHELL_LABEL"], "ProgramArguments": [os.path.join(app, "Contents", "MacOS", os.environ["PRODUCT_EXECUTABLE"])],
         "EnvironmentVariables": {"OPEN_DICTATE_PRODUCT_ENV_PREFIX": prefix, f"{prefix}_LEXICON_ROOT": lexicon},
         "RunAtLoad": True, "KeepAlive": False, "LimitLoadToSessionType": "Aqua", "ProcessType": "Interactive",
         "StandardOutPath": os.path.join(runtime_logs, "shell.out.log"), "StandardErrorPath": os.path.join(runtime_logs, "shell.err.log")}
for path, value in ((daemon_path, daemon), (shell_path, shell)):
    with open(path, "wb") as f: plistlib.dump(value, f)
    os.chmod(path, 0o600)
PY

if [[ -n "$RENDER_DIR" ]]; then
  echo "✓ rendered plists to $PLIST_DIR (nothing installed)"
  exit 0
fi

UID_N="$(id -u)"
launchctl bootout "gui/$UID_N/$PRODUCT_DAEMON_LABEL" 2>/dev/null || true
launchctl bootout "gui/$UID_N/$PRODUCT_SHELL_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$DAEMON_PLIST"
launchctl bootstrap "gui/$UID_N" "$SHELL_PLIST"
echo "✓ installed $PRODUCT_APP_NAME with isolated runtime namespace"
