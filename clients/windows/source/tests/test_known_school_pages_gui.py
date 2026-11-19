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
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_known_school_results(current)
        thread.assert_not_called()
        browser.assert_not_called()
        self.assertIn("旧 HAR", str(current.school_page_status.set.call_args))

    def test_verified_read_uses_worker_no_browser(self):
        current = app()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        done = KnownSchoolPageResult("shape_observed", "selected_results", 1, 2)
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_known_page_contract", return_value=None), \
             patch("windows_gui.read_known_school_page", new_callable=AsyncMock,
                   return_value=done) as reader, \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_known_school_results(current)
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
            browser.assert_not_called()
        reader.assert_awaited_once_with(cfg, "selected_results", session_cookie=None)
        event = current.events.get_nowait()
        self.assertEqual(event.kind, "known_school_results")
        self.assertEqual(event.value.table_count, 1)
        self.assertTrue(current._school_page_read_in_progress)

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
        self.assertNotIn("成功", result.message)

    def test_blocked_when_timetable_active(self):
        current = app()
        current._timetable_read_in_progress = True
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(current)
        thread.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
