"""Headless Windows course-plan button must use the EXE's current session."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.ncwu_course_plan import PlanSnapshot
from windows_gui import Application


class CoursePlanButtonTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None, scheduled_worker=None, _closing=False,
            _native_login_in_progress=False,
            _native_preparation_in_progress=False,
            _session_check_in_progress=False,
            _native_session_cookie=None,
            _native_session_origin=None,
            path=SimpleNamespace(get=lambda: "config.yaml"),
            course_plan_button=Mock(),
            course_plan_status=Mock(),
            events=queue.Queue(),
        )

    def test_without_native_login_sends_no_request(self):
        with patch("windows_gui.read_config") as config, \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_course_plan(self.app)
        config.assert_not_called()
        thread.assert_not_called()
        self.app.course_plan_status.set.assert_called()

    def test_uses_same_session_and_never_opens_browser(self):
        self.app._native_session_cookie = "JSESSIONID=LOCAL_ONLY"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_current_course_plan",
                   new_callable=AsyncMock,
                   return_value=PlanSnapshot("read", (), 0, "table")) as read, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_course_plan(self.app)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"],
                             "KingoCoursePlanRead")
            thread.call_args.kwargs["target"]()
            browser.assert_not_called()
        read.assert_awaited_once_with(
            cfg, session_cookie="JSESSIONID=LOCAL_ONLY")
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "ncwu_course_plan")
        self.assertEqual(event.value.code, "read")

    def test_active_other_s202_read_does_not_start_parallel_plan(self):
        self.app._native_session_cookie = "JSESSIONID=LOCAL_ONLY"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        self.app._s202_score_in_progress = True
        with patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_config") as config:
            Application.check_course_plan(self.app)
        thread.assert_not_called()
        config.assert_not_called()

    def test_active_polling_blocks_additional_plan_reads(self):
        self.app._native_session_cookie = "JSESSIONID=LOCAL_ONLY"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        self.app._live_polling = True
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_course_plan(self.app)
        thread.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
