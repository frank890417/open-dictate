import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_contracts", ROOT / "scripts" / "check-contracts.py")
contracts = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(contracts)


class ContractTests(unittest.TestCase):
    def test_public_product_and_protocol_agree(self):
        protocol = contracts.load_json(ROOT / "contracts" / "protocol.v1.json")
        product = contracts.load_json(ROOT / "contracts" / "product-config.open-dictate.json")
        contracts.validate_protocol(protocol)
        contracts.validate_product(product)
        self.assertEqual(protocol["wireProtocolVersion"], product["runtime"]["wireProtocolVersion"])

    def test_external_lexicon_requires_environment_boundary(self):
        product = contracts.load_json(ROOT / "contracts" / "product-config.open-dictate.json")
        product["lexicon"]["provider"] = "external"
        with self.assertRaises(contracts.ContractError):
            contracts.validate_product(product)

    def test_overlay_must_pin_current_protocol(self):
        lock = {
            "schemaVersion": 1,
            "upstream": {"version": "v1.2.3", "commit": "a" * 40, "sourceArchiveSha256": "b" * 64},
            "compatibility": {"wireProtocolVersion": "9.9", "productConfigSchema": 1, "overlayTests": ["./test-overlay.sh"]},
        }
        with self.assertRaises(contracts.ContractError):
            contracts.validate_lock(lock, "1.0", None)

    def test_overlay_archive_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "source.tar.gz"
            archive.write_bytes(b"release")
            import hashlib
            lock = {
                "schemaVersion": 1,
                "upstream": {"version": "v1.2.3", "commit": "a" * 40,
                             "sourceArchiveSha256": hashlib.sha256(b"release").hexdigest()},
                "compatibility": {"wireProtocolVersion": "1.0", "productConfigSchema": 1,
                                  "overlayTests": ["./test-overlay.sh"]},
            }
            contracts.validate_lock(lock, "1.0", archive)
            archive.write_bytes(b"tampered")
            with self.assertRaises(contracts.ContractError):
                contracts.validate_lock(lock, "1.0", archive)


if __name__ == "__main__":
    unittest.main()
