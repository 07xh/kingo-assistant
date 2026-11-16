"""Headless Tk wiring: EXE login never opens a browser or logs secrets."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from windows_gui import Application, scoped_native_cookie
from core.native_login import NativeLoginResult


class NativeLoginGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None, scheduled_worker=None, _closing=False,
            _native_login_in_progress=False, _native_preparation_in_progress=False,
            _session_check_in_progress=False, _native_session_cookie=None,
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            native_login_status=Mock(), native_login_button=Mock(),
            native_preparation_status=Mock(), events=queue.Queue(),
        )

    def test_unverified_contract_never_asks_password_or_uses_network(self):
        cfg = {"protocol": {"verified": False}, "auth": {}}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.simpledialog.askstring") as dialog, \
             patch("windows_gui.threading.Thread") as thread:
            Application.start_native_login(self.app)
        dialog.assert_not_called()
        thread.assert_not_called()

    def test_verified_login_runs_in_background_without_browser(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        response = NativeLoginResult("verified", "SID=mock-private", "jwmis.ncwu.edu.cn")
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_ncwulogin_entry", return_value=None), \
             patch("windows_gui.simpledialog.askstring", side_effect=["alice", "secret"]) as dialog, \
             patch("windows_gui.login_ncwusigned", new_callable=AsyncMock,
                   return_value=response) as login, \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.start_native_login(self.app)
            self.assertEqual(thread.call_args.kwargs["name"], "KingoNativeLogin")
            thread.call_args.kwargs["target"]()
            browser.assert_not_called()
        self.assertEqual(dialog.call_count, 2)
        login.assert_awaited_once_with(cfg, "alice", "secret")
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "native_login")
        self.assertEqual(event.value.code, "verified")
        self.assertNotIn("secret", event.value.message)

    def test_clear_session_does_not_contact_school(self):
        self.app._native_session_cookie = "SID=private"
        with patch("windows_gui.webbrowser.open") as browser, \
             patch("windows_gui.threading.Thread") as thread:
            Application.clear_native_session(self.app)
        self.assertIsNone(self.app._native_session_cookie)
        browser.assert_not_called()
        thread.assert_not_called()

    def test_native_session_is_only_usable_for_same_origin(self):
        self.app._native_session_cookie = "SID=private"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        self.assertEqual(scoped_native_cookie(
            self.app, {"base_url": "https://jwmis.ncwu.edu.cn"}),
            "SID=private")
        self.assertEqual(scoped_native_cookie(
            self.app, {"base_url": "https://another.example"}), "")
        self.assertNotIn("SID=private", str(scoped_native_cookie(
            self.app, {"base_url": "https://another.example"})))

    def test_session_cannot_be_cleared_during_monitor(self):
        self.app._native_session_cookie = "SID=private"
        self.app.worker = object()
        Application.clear_native_session(self.app)
        self.assertEqual(self.app._native_session_cookie, "SID=private")


if __name__ == "__main__":
    import unittest
    unittest.main()
