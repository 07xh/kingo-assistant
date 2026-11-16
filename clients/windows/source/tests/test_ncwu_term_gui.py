"""Windows Tk wiring for the observed, no-body YearTerm POST."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from windows_gui import Application
from core.ncwu_term_probe import TermProbeResult


def app():
    return SimpleNamespace(
        worker=None, scheduled_worker=None, _closing=False,
        _native_login_in_progress=False,
        _native_preparation_in_progress=False,
        _session_check_in_progress=False,
        _timetable_read_in_progress=False,
        _school_page_read_in_progress=False,
        _term_probe_in_progress=False,
        _native_session_cookie=None, _native_session_origin=None,
        path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
        term_probe_button=Mock(), term_probe_status=Mock(),
        status=Mock(), events=queue.Queue(),
    )


class YearTermGuiTests(TestCase):
    def test_unverified_contract_blocks_locally_without_thread(self):
        inst = app()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "known_year_term": {"verified": False}}
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as worker, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_known_year_term(inst)
        worker.assert_not_called()
        browser.assert_not_called()
        self.assertIn("未联网", str(inst.term_probe_status.set.call_args))

    def test_verified_contract_runs_background_only(self):
        inst = app()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        result = TermProbeResult("shape_observed", 5)
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.validate_term_contract", return_value=None), \
             patch("windows_gui.probe_known_year_term",
                   new_callable=AsyncMock, return_value=result) as probe, \
             patch("windows_gui.threading.Thread") as worker, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_known_year_term(inst)
            worker.assert_called_once()
            worker.call_args.kwargs["target"]()
            browser.assert_not_called()
        probe.assert_awaited_once_with(cfg, session_cookie=None)
        item = inst.events.get_nowait()
        self.assertEqual(item.kind, "known_year_term")
        self.assertEqual(item.value.code, "shape_observed")
        self.assertTrue(inst._term_probe_in_progress)
        inst.term_probe_button.configure.assert_called_once_with(state="disabled")

    def test_active_legacy_timetable_blocks_term_check(self):
        inst = app()
        inst._timetable_read_in_progress = True
        with patch("windows_gui.threading.Thread") as worker:
            Application.check_known_year_term(inst)
        worker.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
