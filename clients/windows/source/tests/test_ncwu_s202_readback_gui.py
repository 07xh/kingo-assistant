"""Headless GUI contracts for the new S202 result readback action."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from windows_gui import Application
from core.ncwu_s202_readback import S202ReadbackResult


class S202GuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None, scheduled_worker=None, _closing=False,
            _native_login_in_progress=False,
            _native_preparation_in_progress=False,
            _session_check_in_progress=False,
            _native_session_cookie=None, _native_session_origin=None,
            _native_verified_user_code=None,
            s202_result_status=Mock(), s202_result_button=Mock(),
            path=SimpleNamespace(get=lambda: "config.yaml"),
            events=queue.Queue(),
        )

    def test_no_session_never_starts_background_request(self):
        with patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_config") as load:
            Application.check_s202_selected_results(self.app)
        thread.assert_not_called()
        load.assert_not_called()
        self.app.s202_result_status.set.assert_called()

    def test_one_background_native_read_and_no_browser(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        self.app._native_session_cookie = "JSESSIONID=LOCAL"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        self.app._native_verified_user_code = "synthetic-account"
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_s202_selected_results",
                   new_callable=AsyncMock,
                   return_value=S202ReadbackResult("page_observed")) as read:
            Application.check_s202_selected_results(self.app)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"],
                             "KingoS202ResultReadback")
            thread.call_args.kwargs["target"]()
        read.assert_awaited_once_with(cfg, session_cookie="JSESSIONID=LOCAL")
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "ncwu_s202_results")
        self.assertEqual(event.value.code, "page_observed")


if __name__ == "__main__":
    import unittest
    unittest.main()
