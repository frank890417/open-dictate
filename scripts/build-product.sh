#!/usr/bin/env bash
# Build a product flavor from the public machine-readable contract.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="${2:?missing config path}"; shift 2 ;;
    --output) OUTPUT="${2:?missing output path}"; shift 2 ;;
    -h|--help) echo "usage: $0 --config <json> --output <dir>"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done
[[ -n "$CONFIG" && -n "$OUTPUT" ]] || { echo "--config and --output are required" >&2; exit 64; }
CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
eval "$(python3 "$ROOT/scripts/product-config-env.py" --config "$CONFIG")"
(cd "$ROOT" && ./build.sh)
SOURCE="$ROOT/dist/$PRODUCT_APP_NAME.app"
TARGET="$OUTPUT/$PRODUCT_APP_NAME.app"
if [[ "$SOURCE" != "$TARGET" ]]; then
  rm -rf "$TARGET"
  ditto "$SOURCE" "$TARGET"
fi
codesign --verify --deep "$TARGET"
echo "✓ $TARGET"
