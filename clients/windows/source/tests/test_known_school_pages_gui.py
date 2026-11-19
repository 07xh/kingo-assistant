"""GUI wiring for previously observed NCWU HTML readback; no live school access."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from windows_gui import Application
from core.known_school_pages import KnownSchoolPageResult


def app():
    return SimpleNamespace(
        worker=None, scheduled_worker=None, _closing=False,
        _native_login_in_progress=False,
        _native_preparation_in_progress=False,
        _timetable_read_in_progress=False,
        _school_page_read_in_progress=False,
        _session_check_in_progress=False,
        _native_session_cookie=None, _native_session_origin=None,
        path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
        school_page_status=Mock(), school_page_button=Mock(),
        status=Mock(), events=queue.Queue(),
    )


class SchoolResultsGuiTests(TestCase):
    def test_unknown_query_blocks_without_thread_or_browser(self):
        current = app()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "known_school_pages": {"selected_results": {"verified": False}}}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
        thread.assert_not_called()
        self.assertIn("旧 HAR", str(current.school_page_status.set.call_args))

    def test_verified_read_uses_worker_no_browser(self):
        current = app()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        done = KnownSchoolPageResult("shape_observed", "selected_results", 1, 2)
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_known_page_contract", return_value=None), \
             patch("windows_gui.read_known_school_page", new_callable=AsyncMock,
                   return_value=done) as reader, \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
        reader.assert_awaited_once_with(cfg, "selected_results", session_cookie=None)
        event = current.events.get_nowait()
        self.assertEqual(event.kind, "known_school_results")
        self.assertEqual(event.value.table_count, 1)
        self.assertTrue(current._school_page_read_in_progress)

    def test_dynamic_query_needs_verified_exe_account_before_worker(self):
        current = app()
        current._native_session_cookie = "SID=EXE-LOCAL"
        current._native_session_origin = "jwmis.ncwu.edu.cn"
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "known_school_pages": {"selected_results": {
                   "query_mode": "current_yearterm_base64"}}}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_known_page_contract", return_value=None), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
        thread.assert_not_called()
        self.assertIn("身份核验", str(current.school_page_status.set.call_args))

    def test_dynamic_query_uses_verified_memory_account_only(self):
        current = app()
        current._native_session_cookie = "SID=EXE-LOCAL"
        current._native_session_origin = "jwmis.ncwu.edu.cn"
        current._native_verified_user_code = "FICTIONAL001"
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "known_school_pages": {"selected_results": {
                   "query_mode": "current_yearterm_base64"}}}
        done = KnownSchoolPageResult("shape_observed", "selected_results", 1, 1)
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_known_page_contract", return_value=None), \
             patch("windows_gui.read_known_school_page", new_callable=AsyncMock,
                   return_value=done) as read, \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
        read.assert_awaited_once_with(
            cfg, "selected_results", session_cookie="SID=EXE-LOCAL",
            expected_user_code="FICTIONAL001",
        )

    def test_clear_native_session_removes_verified_account(self):
        current = app()
        current._native_session_cookie = "SID=EXE-LOCAL"
        current._native_session_origin = "jwmis.ncwu.edu.cn"
        current._native_verified_user_code = "FICTIONAL001"
        current._pending_native_login_account = "PRIVATE-ACCOUNT"
        current.native_login_status = Mock()
        current.native_preparation_status = Mock()
        Application.clear_native_session(current)
        self.assertIsNone(current._native_verified_user_code)
        self.assertIsNone(current._pending_native_login_account)
        self.assertIsNone(current._native_session_cookie)

    def test_account_mismatch_event_clears_exe_verified_identity(self):
        current = app()
        current.after = Mock()
        current.poll = Mock()
        current.log = Mock()
        current.native_login_status = Mock()
        current.native_preparation_status = Mock()
        current._native_session_cookie = "SID=EXE-LOCAL"
        current._native_session_origin = "jwmis.ncwu.edu.cn"
        current._native_verified_user_code = "FICTIONAL001"
        current._pending_native_login_account = None
        current._live_polling = False
        current.events.put(SimpleNamespace(
            kind="known_school_results",
            value=KnownSchoolPageResult("identity_mismatch", "selected_results")))
        Application.poll(current)
        self.assertIsNone(current._native_session_cookie)
        self.assertIsNone(current._native_session_origin)
        self.assertIsNone(current._native_verified_user_code)
        self.assertIn("账号核验失败",
                      str(current.native_login_status.set.call_args))

    def test_known_exact_field_candidates_are_display_only(self):
        from core.ncwu_known_result_candidates import HistoricalClassCandidate
        current = app()
        result = KnownSchoolPageResult(
            "shape_observed", "selected_results", 1, 2,
            candidates=(HistoricalClassCandidate(
                "C101", "INTERNAL-SKBJDM", "未核验"),),
            recognized_identity_tables=1,
        )
        with patch("windows_gui._open_readonly_popup") as popup, \
             patch("windows_gui._create_readonly_tree") as tree:
            Application.show_known_result_candidates(current, result)
        popup.assert_called_once()
        self.assertEqual(tree.return_value.insert.call_count, 1)
        self.assertIn("INTERNAL-SKBJDM",
                      tree.return_value.insert.call_args.kwargs["values"])
        self.assertIn("不能据此认定选课成功、提交许可", result.message)

    def test_blocked_when_timetable_active(self):
        current = app()
        current._timetable_read_in_progress = True
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
        thread.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
