"""GUI regression: offline auth audit never contacts school or prompts login."""
import queue
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from windows_gui import Application
from core.native_login import validate_login_contract


class AuthenticationAuditGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None,
            scheduled_worker=None,
            log=Mock(),
        )

    def test_offline_cas_har_menu_uses_local_file_without_network(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.har"
            path.write_text(json.dumps({"log": {"entries": [
                {"request": {"method": "GET", "url":
                    "https://authserver.ncwu.edu.cn/authserver/login?service="
                    "https%3A%2F%2Fjwmis.ncwu.edu.cn%2Fhsjw%2Fcaslogin"},
                 "response": {"status": 200}}
            ]}}), encoding="utf-8")
            with patch("windows_gui.filedialog.askopenfilename", return_value=str(path)), \
                 patch("windows_gui.messagebox.showinfo") as dialog:
                Application.audit_auth_route_har(self.app)
            self.assertIn("统一认证 CAS", str(dialog.call_args))
            self.app.log.assert_called_once()

    def test_running_worker_blocks_file_dialog(self):
        self.app.worker = object()
        with patch("windows_gui.filedialog.askopenfilename") as chooser, \
             patch("windows_gui.messagebox.showinfo"):
            Application.audit_auth_route_har(self.app)
        chooser.assert_not_called()

    def test_explicit_cas_mode_blocks_login_without_sending_credentials(self):
        config = {"base_url": "https://jwmis.ncwu.edu.cn",
                  "protocol": {"verified": True, "identity_path": "/me",
                               "identity_referer": "/", "identity_selector": "#me"},
                  "auth": {"login": {"mode": "cas", "verified": True}}}
        self.assertEqual(validate_login_contract(config), "cas_adapter_missing")

    def test_unknown_form_mode_cannot_fall_back_to_form_login(self):
        config = {"base_url": "https://jwmis.ncwu.edu.cn",
                  "protocol": {"verified": True, "identity_path": "/me",
                               "identity_referer": "/", "identity_selector": "#me"},
                  "auth": {"login": {"mode": "unknown", "verified": True}}}
        self.assertEqual(validate_login_contract(config), "invalid_contract")


if __name__ == "__main__":
    import unittest
    unittest.main()
