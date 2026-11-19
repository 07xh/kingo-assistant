"""Offline tests for the native GUI readiness detail formatter."""
import unittest

from core.attestation import AttestationError
from core.ncwu_gate_details import format_ncwu_gate_details


class GateDetailsTest(unittest.TestCase):
    def test_empty_registry_is_not_a_write_permission(self):
        result = format_ncwu_gate_details(None, adapter_installed=False)
        self.assertEqual(result.count("证据缺失"), 4)
        self.assertIn("自动执行器：未接入", result)
        self.assertIn("不会据此启用正式选课提交", result)

    def test_valid_evidence_does_not_imply_live_adapter(self):
        class Registry:
            def describe(self):
                return {"rows": [
                    {"gate": gate, "state": "valid"} for gate in (
                        "official_catalog_verified", "official_enrollment_verified",
                        "official_window_verified", "official_write_verified",
                    )
                ]}

        result = format_ncwu_gate_details(Registry(), adapter_installed=True)
        self.assertEqual(result.count("证据有效"), 4)
        self.assertIn("仍须实机验收", result)
        self.assertIn("没有已验证的 S202 提交表单提供器", result)

    def test_broken_registry_fails_closed(self):
        class Broken:
            def describe(self):
                raise AttestationError("bad data")

        result = format_ncwu_gate_details(Broken(), adapter_installed=False)
        self.assertEqual(result.count("证据无效"), 4)
