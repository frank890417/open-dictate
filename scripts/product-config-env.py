#!/usr/bin/env python3
"""Validate a product contract and emit shell-safe build/runtime variables."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_contracts", ROOT / "scripts" / "check-contracts.py")
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(checker)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--require-lexicon", action="store_true")
    args = parser.parse_args()
    try:
        config = checker.load_json(args.config)
        checker.validate_product(config)
    except checker.ContractError as exc:
        print(f"product config invalid: {exc}", file=sys.stderr)
        return 64

    p, r, lex = config["product"], config["runtime"], config["lexicon"]
    values = {
        "PRODUCT_APP_NAME": p["name"],
        "PRODUCT_EXECUTABLE": p["executable"],
        "PRODUCT_ID": p["id"],
        "PRODUCT_BUNDLE_ID": p["bundleIdentifier"],
        "PRODUCT_SOCKET_PATH": r["socketPath"],
        "PRODUCT_DATA_ROOT": p["dataRoot"],
        "PRODUCT_LOG_ROOT": p["logRoot"],
        "PRODUCT_DAEMON_LABEL": p["daemonLaunchAgentLabel"],
        "PRODUCT_SHELL_LABEL": p["shellLaunchAgentLabel"],
        "PRODUCT_ENV_PREFIX": p["environmentPrefix"],
        "PRODUCT_PRIORITY_TERMS": r["priorityTerms"],
        "PRODUCT_LEXICON_PROVIDER": lex["provider"],
    }
    if lex["provider"] == "bundled":
        lexicon_root = ROOT / lex["starterBundle"]
    else:
        names = [lex["externalRootEnvironment"]]
        if lex.get("legacyExternalRootEnvironment"):
            names.append(lex["legacyExternalRootEnvironment"])
        raw = next((os.environ.get(name) for name in names if os.environ.get(name)), None)
        raw = raw or lex.get("defaultExternalRoot")
        if not raw and args.require_lexicon:
            print(f"external lexicon root missing; set {' or '.join(names)}", file=sys.stderr)
            return 66
        lexicon_root = Path(raw).expanduser() if raw else Path("")
    values["PRODUCT_LEXICON_ROOT"] = str(lexicon_root)
    for key, value in values.items():
        print(f"export {key}={shlex.quote(str(value))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
