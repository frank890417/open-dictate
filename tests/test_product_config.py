from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_config(extra_env: dict[str, str]) -> dict:
    code = """
import json
from daemon import product_config as c
print(json.dumps({
  'app': c.APP_NAME, 'product': c.PRODUCT_ID, 'socket': c.SOCKET_PATH,
  'data': str(c.DATA_ROOT), 'log': str(c.LOG_ROOT),
  'lexicon': str(c.LEXICON_ROOT), 'terms': c.PRIORITY_TERMS,
  'daemon': c.DAEMON_LAUNCH_LABEL, 'shell': c.SHELL_LAUNCH_LABEL,
}))
"""
    env = os.environ.copy()
    env.update(extra_env)
    return json.loads(subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env))


def test_default_product_config_is_open_dictate():
    cfg = _load_config({})
    assert cfg["app"] == "OpenDictate"
    assert cfg["product"] == "open-dictate"
    assert cfg["socket"] == "/tmp/open-dictate.sock"
    assert cfg["daemon"] == "org.opendictate.daemon"


def test_private_overlay_uses_selected_prefix(tmp_path):
    cfg = _load_config({
        "OPEN_DICTATE_PRODUCT_ENV_PREFIX": "MUSE_DICTATE",
        "MUSE_DICTATE_APP_NAME": "MuseDictate",
        "MUSE_DICTATE_PRODUCT_ID": "muse-dictate",
        "MUSE_DICTATE_SOCKET_PATH": "/tmp/muse-dictate-test.sock",
        "MUSE_DICTATE_DATA_ROOT": str(tmp_path / "data"),
        "MUSE_DICTATE_LOG_ROOT": str(tmp_path / "logs"),
        "MUSE_DICTATE_LEXICON_ROOT": str(tmp_path / "lexicon"),
        "MUSE_DICTATE_PRIORITY_TERMS": "私人測試詞",
        "MUSE_DICTATE_DAEMON_LABEL": "com.muse.dictate.daemon",
        "MUSE_DICTATE_SHELL_LABEL": "com.muse.dictate.shell",
    })
    assert cfg == {
        "app": "MuseDictate",
        "product": "muse-dictate",
        "socket": "/tmp/muse-dictate-test.sock",
        "data": str(tmp_path / "data"),
        "log": str(tmp_path / "logs"),
        "lexicon": str(tmp_path / "lexicon"),
        "terms": "私人測試詞",
        "daemon": "com.muse.dictate.daemon",
        "shell": "com.muse.dictate.shell",
    }


def test_contract_maps_external_lexicon_through_legacy_environment(tmp_path):
    config = tmp_path / "product.json"
    config.write_text(json.dumps({
        "schemaVersion": 1,
        "product": {
            "id": "test-dictate", "name": "TestDictate", "bundleIdentifier": "dev.test.dictate",
            "executable": "TestDictate", "daemonLaunchAgentLabel": "dev.test.dictate.daemon",
            "shellLaunchAgentLabel": "dev.test.dictate.shell", "environmentPrefix": "TEST_DICTATE",
            "dataRoot": "~/.test-dictate", "logRoot": "~/.test-dictate/log",
        },
        "runtime": {"socketPath": "/tmp/test-dictate.sock", "wireProtocolVersion": "1.0",
                    "updateChannel": "beta", "priorityTerms": "測試詞"},
        "lexicon": {"provider": "external", "starterBundle": "vendor",
                    "externalRootEnvironment": "TEST_DICTATE_LEXICON_ROOT",
                    "legacyExternalRootEnvironment": "MUSE_BOT_ROOT"},
        "adapters": [],
    }), encoding="utf-8")
    env = os.environ.copy()
    env.pop("TEST_DICTATE_LEXICON_ROOT", None)
    env["MUSE_BOT_ROOT"] = str(tmp_path / "memory")
    output = subprocess.check_output([
        sys.executable, str(ROOT / "scripts" / "product-config-env.py"),
        "--config", str(config), "--require-lexicon",
    ], cwd=ROOT, env=env, text=True)
    assert "export PRODUCT_ENV_PREFIX=TEST_DICTATE" in output
    assert f"export PRODUCT_LEXICON_ROOT='{tmp_path / 'memory'}'" in output or f"export PRODUCT_LEXICON_ROOT={tmp_path / 'memory'}" in output
