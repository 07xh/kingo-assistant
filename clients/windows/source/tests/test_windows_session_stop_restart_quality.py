"""Windows session expiry and stop/restart regressions (no school I/O)."""
from __future__ import annotations

import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.native_timetable import TimetableReadResult
from core.ncwu_s202_score import PersonalScoreCount
from windows_gui import (
    Application,
    MonitorEvent,
    _invalidate_native_session,
    _native_read_in_progress,
    _ncwu_read_busy,
)


def app():
    return SimpleNamespace(
        worker=None, scheduled_worker=None,
        _closing=False, _benchmark_in_progress=False,
        _native_login_in_progress=False,
        _native_preparation_in_progress=False,
        _session_check_in_progress=False,
        _native_session_cookie="SID=REDACTED", _native_session_origin="jwmis.ncwu.edu.cn",
        _live_polling=False, _live_poll_timer=None, _live_catalog_in_progress=False,
        native_login_status=Mock(), native_preparation_status=Mock(),
        live_poll_button=Mock(), live_catalog_button=Mock(), live_catalog_status=Mock(),
        timetable_read_button=Mock(), timetable_read_status=Mock(),
        s202_score_button=Mock(), s202_score_status=Mock(),
        start_button=Mock(), stop_button=Mock(), edit_button=Mock(), plans_button=Mock(),
        config_entry=Mock(), pick_button=Mock(), open_button=Mock(),
        status=Mock(), log=Mock(), populate=Mock(),
        path=SimpleNamespace(get=lambda: "config.yaml"),
        events=queue.Queue(), poll=Mock(), after=Mock(), after_cancel=Mock(),
        _tick_portal_appointment=Mock(), check_live_catalog=Mock(),
        stop_live_polling=Mock(),
        show_live_catalog_rows=Mock(),
    )


class SessionLifecycleQualityTests(TestCase):
    def test_all_native_read_flags_block_session_change_and_monitor(self):
        for flag in (
            "_live_catalog_in_progress", "_s202_result_in_progress",
            "_s202_round_probe_in_progress", "_s202_score_in_progress",
            "_course_plan_in_progress", "_timetable_read_in_progress",
            "_school_page_read_in_progress", "_term_probe_in_progress",
        ):
            with self.subTest(flag=flag):
                current = app()
                setattr(current, flag, True)
                self.assertTrue(_native_read_in_progress(current))
                self.assertTrue(_ncwu_read_busy(current))
                Application.clear_native_session(current)
                self.assertEqual(current._native_session_cookie, "SID=REDACTED")
                self.assertIn("再次点击", current.native_login_status.set.call_args.args[0])
                current.native_preparation_status.set.assert_not_called()

    def test_legacy_read_and_identity_probe_wait_for_active_s202_read(self):
        current = app()
        current._live_catalog_in_progress = True
        with patch("windows_gui.read_config") as loader, \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_known_timetable(current)
            Application.check_known_year_term(current)
            Application.check_known_school_results(current)
            Application.check_script_session(current)
        loader.assert_not_called()
        thread.assert_not_called()

    def test_native_login_waits_for_opted_in_refresh_before_asking_password(self):
        current = app()
        current._live_polling = True
        with patch("windows_gui.read_config") as loader, \
             patch("windows_gui.simpledialog.askstring") as prompt:
            Application.start_native_login(current)
        loader.assert_not_called()
        prompt.assert_not_called()

    def test_clear_during_auto_refresh_stops_future_cycles_but_not_inflight_read(self):
        current = app()
        current._live_polling = True
        current._live_catalog_in_progress = True
        current.stop_live_polling = lambda: Application.stop_live_polling(current)
        Application.clear_native_session(current)
        self.assertFalse(current._live_polling)
        self.assertIsNone(current._live_poll_timer)
        self.assertEqual(current._native_session_cookie, "SID=REDACTED")
        self.assertIn("正在执行", current.native_login_status.set.call_args.args[0])
        # Once the in-flight request has drained, a second explicit clear
        # must not silently retain the old session.
        current._live_catalog_in_progress = False
        Application.clear_native_session(current)
        self.assertIsNone(current._native_session_cookie)
        self.assertIsNone(current._native_session_origin)
        self.assertIn("内存会话", current.native_login_status.set.call_args.args[0])

    def test_clear_when_idle_is_not_server_logout(self):
        current = app()
        Application.clear_native_session(current)
        self.assertIsNone(current._native_session_cookie)
        self.assertIn("不代表学校服务端注销",
                      current.native_login_status.set.call_args.args[0])

    def test_expired_any_adapter_clears_preparation_and_stops_polling(self):
        current = app()
        current._live_polling = True
        _invalidate_native_session(current, "请重新登录")
        self.assertIsNone(current._native_session_cookie)
        self.assertIsNone(current._native_session_origin)
        current.stop_live_polling.assert_called_once()
        current.native_preparation_status.set.assert_called_once_with(
            "会话已失效，请重新登录并核验本人课程")

    def test_legacy_read_expiry_is_consistent_with_s202_expiry(self):
        current = app()
        current._live_polling = True
        current.events.put(MonitorEvent(
            "known_timetable", TimetableReadResult("expired")))
        Application.poll(current)
        self.assertIsNone(current._native_session_cookie)
        self.assertIsNone(current._native_session_origin)
        current.stop_live_polling.assert_called_once()
        current.native_preparation_status.set.assert_called_once()
        current.native_login_status.set.assert_called_once_with(
            "课表接口提示登录失效，请重新授权")

    def test_s202_read_expiry_is_consistent_with_legacy_expiry(self):
        current = app()
        current._live_polling = True
        current.events.put(MonitorEvent(
            "ncwu_s202_score", PersonalScoreCount("expired")))
        Application.poll(current)
        self.assertIsNone(current._native_session_cookie)
        current.stop_live_polling.assert_called_once()
        current.native_preparation_status.set.assert_called_once()


class MonitorStartRollbackTests(TestCase):
    def start(self, current, start_error=None):
        worker = Mock()
        if start_error is not None:
            worker.start.side_effect = start_error
        with patch("windows_gui.read_config", return_value={"xnxq": "TEST"}), \
             patch("windows_gui.resolve_state_file",
                   return_value={"xnxq": "TEST"}), \
             patch("windows_gui.inspect_readiness", return_value=[]), \
             patch("windows_gui.split_checks", return_value=([], [])), \
             patch("windows_gui.validate"), \
             patch("windows_gui.scoped_native_cookie", return_value=None), \
             patch("windows_gui.MonitorWorker", return_value=worker) as constructor:
            Application.start_monitor(current)
        return worker, constructor

    def test_worker_start_error_does_not_strand_current_or_next_run(self):
        current = app()
        first, _ = self.start(
            current, RuntimeError("PRIVATE_THREAD_DETAIL"))
        first.start.assert_called_once()
        self.assertIsNone(current.worker)
        self.assertIn("可以重新启动", current.status.set.call_args.args[0])
        self.assertNotIn("PRIVATE_THREAD_DETAIL", str(current.log.call_args))
        current.start_button.configure.assert_not_called()
        current.stop_button.configure.assert_not_called()
        second, _ = self.start(current)
        second.start.assert_called_once()
        self.assertIs(current.worker, second)
        current.start_button.configure.assert_called_with(state="disabled")
        current.stop_button.configure.assert_called_with(state="normal")

    def test_os_thread_error_does_not_strand_current_run(self):
        current = app()
        self.start(current, OSError("PRIVATE_RESOURCE_DATA"))
        self.assertIsNone(current.worker)
        self.assertNotIn("PRIVATE_RESOURCE_DATA", str(current.status.set.call_args))

    def test_stop_waits_for_finished_event_before_allowing_restart(self):
        current = app()
        worker, _ = self.start(current)
        Application.stop_monitor(current)
        worker.stop.assert_called_once()
        current.stop_button.configure.assert_called_with(state="disabled")
        current.status.set.assert_called_with("正在停止…")
        with patch("windows_gui.read_config") as config:
            Application.start_monitor(current)
        config.assert_not_called()
        self.assertIs(current.worker, worker)
        current.events.put(MonitorEvent("finished", "stopped"))
        Application.poll(current)
        self.assertIsNone(current.worker)
        current.start_button.configure.assert_called_with(state="normal")
        current.stop_button.configure.assert_called_with(state="disabled")
        current.status.set.assert_called_with("已停止｜后台只读监测已结束")
        replacement, _ = self.start(current)
        self.assertIs(current.worker, replacement)
        self.assertIsNot(worker, replacement)

    def test_active_read_or_auto_refresh_blocks_monitor_without_network(self):
        current = app()
        current._live_catalog_in_progress = True
        with patch("windows_gui.read_config") as reader:
            Application.start_monitor(current)
        reader.assert_not_called()
        current._live_catalog_in_progress = False
        current._live_polling = True
        with patch("windows_gui.read_config") as reader:
            Application.start_monitor(current)
        reader.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
