"""Headless Tk wiring for the separate S202 time-window one-shot probe."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.ncwu_s202_round import S202RoundStatus
from windows_gui import Application


class RoundGUIWiringTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None, scheduled_worker=None, _closing=False,
            _native_login_in_progress=False,
            _native_preparation_in_progress=False,
            _session_check_in_progress=False,
            _native_session_cookie=None,
            _native_session_origin=None,
            path=SimpleNamespace(get=lambda: "config.yaml"),
            s202_round_button=Mock(), s202_round_status=Mock(),
            events=queue.Queue(),
        )

    def test_without_native_session_no_request(self):
        with patch("windows_gui.read_config") as load, \
             patch("windows_gui.threading.Thread") as worker:
            Application.check_s202_round(self.app)
        load.assert_not_called()
        worker.assert_not_called()
        self.app.s202_round_status.set.assert_called_once()

    def test_background_probe_reuses_native_session(self):
        self.app._native_session_cookie = "JSESSIONID=LOCAL_TEST"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.probe_s202_round", new_callable=AsyncMock,
                   return_value=S202RoundStatus(
                       "observed", "closed", 3)) as probe:
            Application.check_s202_round(self.app)
            thread.assert_called_once()
            self.assertEqual(
                thread.call_args.kwargs["name"], "KingoS202RoundProbe")
            thread.call_args.kwargs["target"]()
        probe.assert_awaited_once_with(cfg, session_cookie="JSESSIONID=LOCAL_TEST")
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "ncwu_s202_round")
        self.assertEqual(event.value.window, "closed")


if __name__ == "__main__":
    import unittest
    unittest.main()
