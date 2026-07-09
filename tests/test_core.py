"""
Basic automated tests for DevCapsule's core, host-independent logic.
Run with: python -m pytest tests/ -v   (or: python -m unittest tests.test_core)
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devcapsule.security import SecurityManager
from devcapsule.optimizer import HardwareOptimizer, HardwareProfile


class TestSecurityManager(unittest.TestCase):
    def setUp(self):
        self.sec = SecurityManager()

    def test_scrub_redacts_api_key(self):
        data = {"config": {"apiKey": "sk-abcdefghijklmnopqrstuvwx", "theme": "dark"}}
        clean, findings = self.sec.scrub(data)
        self.assertEqual(clean["config"]["apiKey"], "***REDACTED***")
        self.assertEqual(clean["config"]["theme"], "dark")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["value"], "sk-abcdefghijklmnopqrstuvwx")

    def test_scrub_leaves_normal_values_alone(self):
        data = {"editor": "vscode", "fontSize": 14, "extensions": ["ms-python.python"]}
        clean, findings = self.sec.scrub(data)
        self.assertEqual(clean, data)
        self.assertEqual(findings, [])

    def test_vault_roundtrip(self):
        findings = [{"path": "a.b", "value": "super-secret-token-value"}]
        vault_path = Path("/tmp/_devcapsule_test.vault")
        self.sec.encrypt_vault(findings, "correct-password", vault_path)
        recovered = self.sec.decrypt_vault(vault_path, "correct-password")
        self.assertEqual(recovered, findings)
        vault_path.unlink(missing_ok=True)

    def test_vault_wrong_password_fails(self):
        findings = [{"path": "a.b", "value": "secret"}]
        vault_path = Path("/tmp/_devcapsule_test2.vault")
        self.sec.encrypt_vault(findings, "right-pw", vault_path)
        with self.assertRaises(ValueError):
            self.sec.decrypt_vault(vault_path, "wrong-pw")
        vault_path.unlink(missing_ok=True)

    def test_generic_hex_secret_detected(self):
        value = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        self.assertTrue(self.sec._looks_sensitive("some_hash", value))


class TestHardwareOptimizer(unittest.TestCase):
    def setUp(self):
        self.opt = HardwareOptimizer()

    def test_downgrade_detected(self):
        old = HardwareProfile(ram_gb=32, cpu_cores=16, has_gpu=True, gpu_names=["RTX 4090"])
        new = HardwareProfile(ram_gb=8, cpu_cores=4, has_gpu=False)
        result = self.opt.analyze(old, new)
        self.assertTrue(result["downgraded"])
        self.assertTrue(len(result["warnings"]) > 0)

    def test_upgrade_no_warning(self):
        old = HardwareProfile(ram_gb=8, cpu_cores=4, has_gpu=False)
        new = HardwareProfile(ram_gb=32, cpu_cores=16, has_gpu=True, gpu_names=["RTX 4090"])
        result = self.opt.analyze(old, new)
        self.assertFalse(result["downgraded"])
        self.assertEqual(result["warnings"], [])

    def test_model_specific_note_for_oversized_model(self):
        old = HardwareProfile(ram_gb=32, cpu_cores=16, has_gpu=True)
        new = HardwareProfile(ram_gb=8, cpu_cores=4, has_gpu=False)
        result = self.opt.analyze(old, new, configured_ollama_models=["codellama:34b-q4"])
        self.assertTrue(any("34b" in n for n in result["model_specific_notes"]))


if __name__ == "__main__":
    unittest.main()
