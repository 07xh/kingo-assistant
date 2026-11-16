"""Headless GUI wiring tests: native timetable uses the verified-only worker."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application


class KnownTimetableGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None,
            scheduled_worker=None,
            _closing=False,
            _native_login_in_progress=False,
            _native_preparation_in_progress=False,
            _session_check_in_progress=False,
            _timetable_read_in_progress=False,
            _native_session_cookie=None,
            _native_session_origin=None,
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            timetable_read_status=Mock(),
            timetable_read_button=Mock(),
            events=queue.Queue(),
        )

    def test_in_exe_read_does_not_open_browser(self):
        config = {
            "base_url": "https://jwmis.ncwu.edu.cn",
            "known_timetable": {"verified": False},
        }
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_known_timetable(self.app)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"], "KingoKnownTimetableRead")
            thread.call_args.kwargs["target"]()
            browser.assert_not_called()
        result = self.app.events.get_nowait()
        self.assertEqual(result.kind, "known_timetable")
        self.assertEqual(result.value.code, "route_unverified")
        self.app.timetable_read_button.configure.assert_called_with(state="disabled")

    def test_running_login_blocks_timetable_read(self):
        self.app._native_login_in_progress = True
        with patch("windows_gui.threading.Thread") as worker:
            Application.check_known_timetable(self.app)
        worker.assert_not_called()

    def test_existing_monitor_blocks_timetable_read(self):
        self.app.worker = object()
        with patch("windows_gui.threading.Thread") as worker:
            Application.check_known_timetable(self.app)
        worker.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
