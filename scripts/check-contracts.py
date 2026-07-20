#!/usr/bin/env python3
"""Validate Open Dictate contracts without third-party dependencies.

This intentionally checks the stable subset used by the release and private
overlay pipeline. JSON Schema documents remain the portable integration API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


class ContractError(ValueError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path}: root must be an object")
    return value


def require_keys(value: dict, required: set[str], allowed: set[str], where: str) -> None:
    missing = required - value.keys()
    extra = value.keys() - allowed
    if missing:
        raise ContractError(f"{where}: missing {sorted(missing)}")
    if extra:
        raise ContractError(f"{where}: unknown keys {sorted(extra)}")


def require_pattern(value: object, pattern: str, where: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ContractError(f"{where}: invalid value {value!r}")
    return value


def validate_protocol(value: dict) -> None:
    require_keys(value, {"schemaVersion", "wireProtocolVersion", "transport", "commands", "errors"},
                 {"schemaVersion", "wireProtocolVersion", "transport", "commands", "errors"}, "protocol")
    if value["schemaVersion"] != 1:
        raise ContractError("protocol.schemaVersion: unsupported")
    require_pattern(value["wireProtocolVersion"], r"\d+\.\d+", "protocol.wireProtocolVersion")
    transport = value["transport"]
    if transport != {"kind": "unix-domain-socket", "framing": "newline-delimited-json", "socketMode": "0600"}:
        raise ContractError("protocol.transport: unsupported transport or permissions")
    expected_commands = {"transcribe", "ping", "reload_lexicon", "add_pair", "stats"}
    expected_errors = {"no_speech", "file_not_found", "asr_failed", "bad_request", "unknown_cmd", "add_pair_failed"}
    if not isinstance(value["commands"], list) or set(value["commands"]) != expected_commands:
        raise ContractError("protocol.commands: command set drifted")
    if not isinstance(value["errors"], list) or set(value["errors"]) != expected_errors:
        raise ContractError("protocol.errors: error set drifted")


def validate_product(value: dict) -> None:
    require_keys(value, {"schemaVersion", "product", "runtime", "lexicon"},
                 {"schemaVersion", "product", "runtime", "lexicon", "adapters"}, "product config")
    if value["schemaVersion"] != 1:
        raise ContractError("product.schemaVersion: unsupported")
    product = value["product"]
    product_keys = {"id", "name", "bundleIdentifier", "executable", "daemonLaunchAgentLabel",
                    "shellLaunchAgentLabel", "environmentPrefix", "dataRoot", "logRoot"}
    require_keys(product, product_keys, product_keys, "product.product")
    if not isinstance(product["name"], str) or not product["name"]:
        raise ContractError("product.product.name: required")
    dotted = r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    require_pattern(product["bundleIdentifier"], dotted, "product.product.bundleIdentifier")
    require_pattern(product["id"], r"[a-z0-9][a-z0-9-]*", "product.product.id")
    require_pattern(product["executable"], r"[A-Za-z0-9_-]+", "product.product.executable")
    require_pattern(product["daemonLaunchAgentLabel"], dotted, "product.product.daemonLaunchAgentLabel")
    require_pattern(product["shellLaunchAgentLabel"], dotted, "product.product.shellLaunchAgentLabel")
    require_pattern(product["environmentPrefix"], r"[A-Z][A-Z0-9_]+", "product.product.environmentPrefix")
    for key in ("dataRoot", "logRoot"):
        if not isinstance(product[key], str) or not product[key]:
            raise ContractError(f"product.product.{key}: required")
    runtime = value["runtime"]
    runtime_keys = {"socketPath", "wireProtocolVersion", "updateChannel", "priorityTerms"}
    require_keys(runtime, runtime_keys, runtime_keys, "product.runtime")
    require_pattern(runtime["socketPath"], r"/tmp/[A-Za-z0-9._-]+\.sock", "product.runtime.socketPath")
    require_pattern(runtime["wireProtocolVersion"], r"\d+\.\d+", "product.runtime.wireProtocolVersion")
    if runtime["updateChannel"] not in {"stable", "beta"}:
        raise ContractError("product.runtime.updateChannel: must be stable or beta")
    lexicon = value["lexicon"]
    require_keys(lexicon, {"provider", "starterBundle"},
                 {"provider", "starterBundle", "externalRootEnvironment", "legacyExternalRootEnvironment", "defaultExternalRoot"}, "product.lexicon")
    if lexicon["provider"] not in {"bundled", "external"}:
        raise ContractError("product.lexicon.provider: unsupported")
    if lexicon["provider"] == "external" and "externalRootEnvironment" not in lexicon:
        raise ContractError("product.lexicon.externalRootEnvironment: required for external provider")
    if "externalRootEnvironment" in lexicon:
        require_pattern(lexicon["externalRootEnvironment"], r"[A-Z][A-Z0-9_]+", "product.lexicon.externalRootEnvironment")
    if "legacyExternalRootEnvironment" in lexicon:
        require_pattern(lexicon["legacyExternalRootEnvironment"], r"[A-Z][A-Z0-9_]+", "product.lexicon.legacyExternalRootEnvironment")
    adapters = value.get("adapters", [])
    if not isinstance(adapters, list) or len(adapters) != len(set(adapters)):
        raise ContractError("product.adapters: must be a unique array")
    for index, adapter in enumerate(adapters):
        require_pattern(adapter, r"[a-z0-9][a-z0-9._-]*", f"product.adapters[{index}]")


def validate_lock(value: dict, protocol_version: str, archive: Path | None) -> None:
    require_keys(value, {"schemaVersion", "upstream", "compatibility"},
                 {"schemaVersion", "upstream", "compatibility"}, "overlay lock")
    if value["schemaVersion"] != 1:
        raise ContractError("overlay.schemaVersion: unsupported")
    upstream = value["upstream"]
    require_keys(upstream, {"version", "commit", "sourceArchiveSha256"},
                 {"version", "commit", "sourceArchiveSha256"}, "overlay.upstream")
    require_pattern(upstream["version"], r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", "overlay.upstream.version")
    require_pattern(upstream["commit"], r"[0-9a-f]{40}", "overlay.upstream.commit")
    expected_hash = require_pattern(upstream["sourceArchiveSha256"], r"[0-9a-f]{64}", "overlay.upstream.sourceArchiveSha256")
    compatibility = value["compatibility"]
    require_keys(compatibility, {"wireProtocolVersion", "productConfigSchema", "overlayTests"},
                 {"wireProtocolVersion", "productConfigSchema", "overlayTests"}, "overlay.compatibility")
    if compatibility["wireProtocolVersion"] != protocol_version:
        raise ContractError("overlay.compatibility.wireProtocolVersion: does not match upstream contract")
    if compatibility["productConfigSchema"] != 1:
        raise ContractError("overlay.compatibility.productConfigSchema: unsupported")
    if not isinstance(compatibility["overlayTests"], list) or not compatibility["overlayTests"]:
        raise ContractError("overlay.compatibility.overlayTests: at least one test command required")
    if archive is not None:
        actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ContractError(f"{archive}: SHA-256 does not match overlay lock")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-config", type=Path, default=CONTRACTS / "product-config.open-dictate.json")
    parser.add_argument("--overlay-lock", type=Path)
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    try:
        for schema in CONTRACTS.glob("*.schema.json"):
            document = load_json(schema)
            if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                raise ContractError(f"{schema}: must declare JSON Schema draft 2020-12")
        protocol = load_json(CONTRACTS / "protocol.v1.json")
        validate_protocol(protocol)
        product = load_json(args.product_config)
        validate_product(product)
        if product["runtime"]["wireProtocolVersion"] != protocol["wireProtocolVersion"]:
            raise ContractError("product runtime wireProtocolVersion does not match protocol manifest")
        if args.overlay_lock:
            validate_lock(load_json(args.overlay_lock), protocol["wireProtocolVersion"], args.source_archive)
        elif args.source_archive:
            raise ContractError("--source-archive requires --overlay-lock")
    except ContractError as exc:
        print(f"contract check failed: {exc}", file=sys.stderr)
        return 1
    print(f"contract check passed: wire protocol {protocol['wireProtocolVersion']}, product {product['product']['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
