"""Regression tests for historic NCWU read actions after GUI-only dedup.

Each HTTP adapter retains its own contract and Cookie path. These tests only
assert the Tk worker still invokes the right adapter, event, and fallback.
"""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.known_school_pages import KnownSchoolPageResult
from core.native_timetable import TimetableReadResult
from core.ncwu_term_probe import TermProbeResult
from windows_gui import Application


def ui():
    return SimpleNamespace(
        worker=None, scheduled_worker=None, _closing=False,
        _native_login_in_progress=False, _native_preparation_in_progress=False,
        _session_check_in_progress=False,
        _timetable_read_in_progress=False, _school_page_read_in_progress=False,
        _term_probe_in_progress=False,
        _native_session_cookie="JSESSIONID=OWN_TEST_ONLY",
        _native_session_origin="jwmis.ncwu.edu.cn",
        path=SimpleNamespace(get=lambda: "config.yaml"), events=queue.Queue(),
        timetable_read_status=Mock(), timetable_read_button=Mock(),
        term_probe_status=Mock(), term_probe_button=Mock(),
        school_page_status=Mock(), school_page_button=Mock(),
        status=Mock(),
    )


CONFIG = {"base_url": "https://jwmis.ncwu.edu.cn"}


class OldReadWorkersAfterRefactor(TestCase):
    def _run(self, app, action, read_name, result, *, extra_patches=()):
        patches = [
            patch("windows_gui.read_config", return_value=CONFIG),
            patch("windows_gui.threading.Thread"),
            patch("windows_gui." + read_name, new_callable=AsyncMock,
                  return_value=result),
        ]
        patches.extend(extra_patches)
        started = []
        try:
            for p in patches:
                started.append(p.__enter__())
            action(app)
            thread = started[1]
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.call_args.kwargs["target"]()
            started[2].assert_awaited_once()
            return thread.call_args.kwargs["name"], app.events.get_nowait()
        finally:
            for p in reversed(patches[:len(started)]):
                p.__exit__(None, None, None)

    def test_timetable_keeps_its_own_read_function_and_event(self):
        app = ui()
        name, event = self._run(
            app, Application.check_known_timetable,
            "read_known_timetable", TimetableReadResult("schema_matched", 2, 1))
        self.assertEqual(name, "KingoKnownTimetableRead")
        self.assertEqual(event.kind, "known_timetable")
        self.assertEqual(event.value.weekday_entries, 2)
        self.assertTrue(app._timetable_read_in_progress)
        app.timetable_read_button.configure.assert_called_once_with(state="disabled")

    def test_year_term_requires_independent_contract_before_worker(self):
        app = ui()
        with patch("windows_gui.validate_term_contract", return_value=None):
            name, event = self._run(
                app, Application.check_known_year_term,
                "probe_known_year_term", TermProbeResult("shape_observed", 5))
        self.assertEqual(name, "KingoKnownYearTerm")
        self.assertEqual(event.kind, "known_year_term")
        self.assertEqual(event.value.fields_matched, 5)
        self.assertTrue(app._term_probe_in_progress)

    def test_invalid_year_term_contract_sends_no_request(self):
        app = ui()
        with patch("windows_gui.read_config", return_value=CONFIG), \
             patch("windows_gui.validate_term_contract", return_value="route_unverified"), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_year_term(app)
        thread.assert_not_called()
        app.term_probe_status.set.assert_called_once()
        app.status.set.assert_called_once()

    def test_selected_results_includes_original_page_kind(self):
        app = ui()
        with patch("windows_gui.validate_known_page_contract", return_value=None):
            name, event = self._run(
                app, Application.check_known_school_results,
                "read_known_school_page",
                KnownSchoolPageResult("shape_observed", "selected_results", 1, 2))
        self.assertEqual(name, "KingoKnownSchoolResults")
        self.assertEqual(event.kind, "known_school_results")
        self.assertEqual(event.value.page_kind, "selected_results")
        self.assertTrue(app._school_page_read_in_progress)

    def test_invalid_selected_results_contract_sends_no_request(self):
        app = ui()
        with patch("windows_gui.read_config", return_value=CONFIG), \
             patch("windows_gui.validate_known_page_contract", return_value="query_unverified"), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_school_results(app)
        thread.assert_not_called()
        self.assertEqual(
            app.school_page_status.set.call_args.args[0],
            KnownSchoolPageResult("query_unverified", "selected_results").message)

    def test_selected_results_worker_error_keeps_page_kind_and_hides_secret(self):
        app = ui()
        with patch("windows_gui.read_config", return_value=CONFIG), \
             patch("windows_gui.validate_known_page_contract", return_value=None), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_known_school_page", new_callable=AsyncMock,
                   side_effect=ValueError("SENSITIVE_SESSION")):
            Application.check_known_school_results(app)
            thread.call_args.kwargs["target"]()
        event = app.events.get_nowait()
        self.assertEqual(event.value.code, "unavailable")
        self.assertEqual(event.value.page_kind, "selected_results")
        self.assertNotIn("SENSITIVE_SESSION", repr(event))

    def test_start_failure_restores_original_button_and_message(self):
        app = ui()
        with patch("windows_gui.read_config", return_value=CONFIG), \
             patch("windows_gui.threading.Thread", side_effect=RuntimeError):
            Application.check_known_timetable(app)
        self.assertFalse(app._timetable_read_in_progress)
        self.assertEqual([c.kwargs["state"] for c in
                          app.timetable_read_button.configure.call_args_list],
                         ["disabled", "normal"])
        self.assertEqual(app.timetable_read_status.set.call_args.args[0],
                         "课表读取线程启动失败；没有联网")


if __name__ == "__main__":
    import unittest
    unittest.main()
