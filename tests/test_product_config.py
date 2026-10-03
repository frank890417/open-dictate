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


# --- optional runtime/product fields: model, core terms, signing identity, runtime log root ---

def _product(tmp_path, **overrides):
    cfg = {
        "schemaVersion": 1,
        "product": {
            "id": "test-dictate", "name": "TestDictate", "bundleIdentifier": "dev.test.dictate",
            "executable": "TestDictate", "daemonLaunchAgentLabel": "dev.test.dictate.daemon",
            "shellLaunchAgentLabel": "dev.test.dictate.shell", "environmentPrefix": "TEST_DICTATE",
            "dataRoot": str(tmp_path / "data"), "logRoot": str(tmp_path / "data" / "log"),
        },
        "runtime": {"socketPath": "/tmp/test-dictate.sock", "wireProtocolVersion": "1.0",
                    "updateChannel": "beta", "priorityTerms": "甲、乙、Taiwan.md"},
        "lexicon": {"provider": "bundled", "starterBundle": "vendor"},
        "adapters": [],
    }
    for section, values in overrides.items():
        cfg[section].update(values)
    path = tmp_path / "product.json"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def _env_exports(config):
    out = subprocess.check_output([sys.executable, str(ROOT / "scripts" / "product-config-env.py"),
                                   "--config", str(config)], cwd=ROOT, text=True)
    return out


def _render(tmp_path, config):
    import plistlib
    out = tmp_path / "plists"
    out.mkdir(exist_ok=True)
    subprocess.check_call(["bash", str(ROOT / "scripts" / "install-product.sh"),
                           "--config", str(config), "--render-dir", str(out)],
                          cwd=ROOT, stdout=subprocess.DEVNULL)
    with open(out / "dev.test.dictate.daemon.plist", "rb") as f:
        daemon = plistlib.load(f)
    with open(out / "dev.test.dictate.shell.plist", "rb") as f:
        shell = plistlib.load(f)
    return daemon, shell


def test_optional_fields_default_to_empty_and_runtime_logs_under_data_root(tmp_path):
    config = _product(tmp_path)
    out = _env_exports(config)
    assert "export PRODUCT_MODEL=''" in out
    assert "export PRODUCT_PROMPT_CORE_TERMS=''" in out
    assert "export PRODUCT_SIGNING_IDENTITY=''" in out
    assert f"PRODUCT_RUNTIME_LOG_ROOT={tmp_path / 'data' / 'runtime-logs'}" in out
    daemon, _ = _render(tmp_path, config)
    env = daemon["EnvironmentVariables"]
    # unset fields must leave the daemon on its built-in defaults
    assert "TEST_DICTATE_MODEL" not in env
    assert "TEST_DICTATE_PROMPT_CORE_TERMS" not in env
    assert daemon["StandardOutPath"] == str(tmp_path / "data" / "runtime-logs" / "daemon.out.log")


def test_model_core_terms_priority_terms_and_log_root_reach_the_daemon_environment(tmp_path):
    terms = "Taiwan.md、IRCAM、聲鬥陣、TouchDesigner"
    config = _product(tmp_path,
                      runtime={"model": "mlx-community/whisper-large-v3-mlx",
                               "promptCoreTerms": "測試甲、測試乙、Zed", "priorityTerms": terms},
                      product={"runtimeLogRoot": str(tmp_path / "logs-elsewhere"),
                               "signingIdentity": "TestDictate Local Signing"})
    daemon, shell = _render(tmp_path, config)
    env = daemon["EnvironmentVariables"]
    assert env["TEST_DICTATE_MODEL"] == "mlx-community/whisper-large-v3-mlx"
    assert env["TEST_DICTATE_PROMPT_CORE_TERMS"] == "測試甲、測試乙、Zed"
    assert env["TEST_DICTATE_PRIORITY_TERMS"] == terms
    assert daemon["StandardOutPath"] == str(tmp_path / "logs-elsewhere" / "daemon.out.log")
    assert daemon["StandardErrorPath"] == str(tmp_path / "logs-elsewhere" / "daemon.err.log")
    assert shell["StandardOutPath"] == str(tmp_path / "logs-elsewhere" / "shell.out.log")
    assert "export PRODUCT_SIGNING_IDENTITY='TestDictate Local Signing'" in _env_exports(config)


def test_daemon_reads_model_and_core_terms_through_the_selected_prefix(tmp_path):
    code = ("from daemon import dictated as d; "
            "print(d.MODEL); print('|'.join(d.PROMPT_CORE_TERMS))")
    env = os.environ.copy()
    env.update({"OPEN_DICTATE_PRODUCT_ENV_PREFIX": "TEST_DICTATE",
                "TEST_DICTATE_MODEL": "mlx-community/whisper-large-v3-mlx",
                "TEST_DICTATE_PROMPT_CORE_TERMS": "測試甲、測試乙",
                "TEST_DICTATE_DATA_ROOT": str(tmp_path / "d")})
    try:
        out = subprocess.check_output([sys.executable, "-c", code], cwd=ROOT, env=env, text=True,
                                      stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        import pytest
        pytest.skip("daemon imports (numpy/mlx_whisper) unavailable in this interpreter")
    assert out.split("\n")[:2] == ["mlx-community/whisper-large-v3-mlx", "測試甲|測試乙"]


def test_invalid_optional_fields_are_rejected(tmp_path):
    for overrides in ({"runtime": {"model": "bad model; rm -rf /"}},
                      {"runtime": {"promptCoreTerms": 5}},
                      {"product": {"signingIdentity": "  "}},
                      {"product": {"runtimeLogRoot": ""}}):
        config = _product(tmp_path, **overrides)
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "product-config-env.py"),
                               "--config", str(config)], cwd=ROOT, capture_output=True, text=True)
        assert proc.returncode == 64, overrides
        assert "product config invalid" in proc.stderr


def test_build_script_takes_the_signing_identity_from_the_product_contract():
    build = (ROOT / "build.sh").read_text(encoding="utf-8")
    assert 'SIGN_ID="${PRODUCT_SIGNING_IDENTITY:-OpenDictate Local Signing}"' in build
    # the default flavor still ships no identity override
    default = json.loads((ROOT / "contracts" / "product-config.open-dictate.json").read_text())
    assert "signingIdentity" not in default["product"]
