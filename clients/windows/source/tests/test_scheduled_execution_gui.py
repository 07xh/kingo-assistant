"""Offline Windows GUI bridge tests; never start a live NCWU gateway."""
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core.enrollment import RunReport
from core.enrollment_schedule import ScheduledRunReport
from windows_gui import Application, MonitorEvent


def stub():
    app = SimpleNamespace(
        worker=None,
        scheduled_worker=None,
        _engine_factory=None,
        _closing=False,
        _tick_portal_appointment=Mock(),
        path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
        status=Mock(),
        log=Mock(),
        events=queue.Queue(),
        _tray=Mock(),
        destroy=Mock(),
        after=Mock(),
        poll=Mock(),
    )
    for name in (
        "scheduled_start_button", "scheduled_stop_button", "start_button",
        "stop_button", "edit_button", "plans_button",
    ):
        setattr(app, name, Mock())
    return app


class ScheduledGuiTests(unittest.TestCase):
    def test_missing_real_adapter_never_opens_browser_or_starts_worker(self):
        app = stub()
        with patch("windows_gui.read_config",
                   side_effect=AssertionError("config not needed")), \
             patch("windows_gui.webbrowser.open",
                   side_effect=AssertionError("browser not allowed")), \
             patch("windows_gui.DesktopScheduledWorker",
                   side_effect=AssertionError("no adapter")):
            Application.start_scheduled_enrollment(app)
        self.assertIn("未接入", str(app.status.set.call_args))
        self.assertIsNone(app.scheduled_worker)

    def test_verified_factory_registers_only_when_idle(self):
        app = stub()
        factory = Mock()
        Application.install_verified_engine_factory(app, factory)
        self.assertIs(app._engine_factory, factory)
        app.scheduled_start_button.configure.assert_called_once_with(state="normal")
        app.scheduled_worker = Mock()
        with self.assertRaises(RuntimeError):
            Application.install_verified_engine_factory(app, Mock())

    def test_user_must_confirm_real_write_before_worker_starts(self):
        app = stub()
        app._engine_factory = Mock()
        with patch("windows_gui.read_config", return_value={}) as read, \
             patch("windows_gui.prepare_scheduled_plan", return_value=SimpleNamespace(
                 target_time="2026-12-21 09:00:00", targets=[object()]
             )), \
             patch("windows_gui.messagebox.askyesno", return_value=False), \
             patch("windows_gui.DesktopScheduledWorker") as scheduled:
            Application.start_scheduled_enrollment(app)
        read.assert_called_once_with(Path("config.yaml"), offline=True)
        scheduled.assert_not_called()
        self.assertIsNone(app.scheduled_worker)

    def test_confirmed_run_starts_inprocess_not_browser(self):
        app = stub()
        app._engine_factory = Mock()
        fake_plan = SimpleNamespace(
            target_time="2026-12-21 09:00:00", targets=[object()]
        )
        with patch("windows_gui.read_config", return_value={}), \
             patch("windows_gui.prepare_scheduled_plan", return_value=fake_plan), \
             patch("windows_gui.messagebox.askyesno", return_value=True), \
             patch("windows_gui.webbrowser.open",
                   side_effect=AssertionError("unexpected browser launch")), \
             patch("windows_gui.DesktopScheduledWorker") as scheduled:
            Application.start_scheduled_enrollment(app)
        scheduled.assert_called_once()
        self.assertIs(scheduled.call_args.args[0], fake_plan)
        self.assertIs(scheduled.call_args.args[1], app._engine_factory)
        scheduled.return_value.start.assert_called_once()
        app.scheduled_stop_button.configure.assert_called_with(state="normal")
        app.start_button.configure.assert_called_with(state="disabled")

    def test_inprocess_run_rejects_concurrent_session_probe(self):
        app = stub()
        app._engine_factory = Mock()
        app._session_check_in_progress = True
        with patch("windows_gui.DesktopScheduledWorker") as worker, \
             patch("windows_gui.messagebox.showinfo") as dialog:
            Application.start_scheduled_enrollment(app)
        worker.assert_not_called()
        dialog.assert_called_once()

    def test_script_session_probe_rejects_concurrent_native_run(self):
        app = stub()
        app.scheduled_worker = Mock()
        app._session_check_in_progress = False
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_script_session(app)
        thread.assert_not_called()

    def test_verified_execution_cancels_existing_browser_fallback(self):
        app = stub()
        app._engine_factory = Mock()
        app._official_portal_appointment = object()
        app.portal_appointment_button = Mock()
        app.portal_appointment_status = Mock()
        plan = SimpleNamespace(
            target_time="2026-12-21 09:00:00", targets=[object()]
        )
        with patch("windows_gui.read_config", return_value={}), \
             patch("windows_gui.prepare_scheduled_plan", return_value=plan), \
             patch("windows_gui.messagebox.askyesno", return_value=True), \
             patch("windows_gui.DesktopScheduledWorker"):
            Application.start_scheduled_enrollment(app)
        self.assertIsNone(app._official_portal_appointment)
        self.assertIn("已取消", str(app.portal_appointment_status.set.call_args))

    def test_cleanup_warning_does_not_replace_official_result(self):
        app = stub()
        app.scheduled_worker = object()
        app.events.put(MonitorEvent("scheduled_cleanup_warning", "RuntimeError"))
        app.events.put(MonitorEvent("scheduled_result", ScheduledRunReport(
            "engine_halted", RunReport({"A": "unknown"}, True), 2, 0.0
        )))
        app.events.put(MonitorEvent("scheduled_finished"))
        Application.poll(app)
        self.assertTrue(any("适配器资源清理" in str(c)
                            for c in app.log.call_args_list))
        self.assertTrue(any("保护性停止" in str(c)
                            for c in app.status.set.call_args_list))

    def test_stop_is_cooperative_and_not_a_kill(self):
        app = stub()
        app.scheduled_worker = Mock()
        Application.stop_scheduled_enrollment(app)
        app.scheduled_worker.stop.assert_called_once()
        self.assertIn("完成官方结果核验", str(app.status.set.call_args))

    def test_task_finish_restores_controls_but_not_live_adapter(self):
        app = stub()
        app.scheduled_worker = object()
        app.events.put(MonitorEvent("scheduled_result", ScheduledRunReport(
            "window_unknown", RunReport({"A": "blocked"}, True), 1, 0.0
        )))
        app.events.put(MonitorEvent("scheduled_finished"))
        Application.poll(app)
        self.assertIsNone(app.scheduled_worker)
        app.scheduled_start_button.configure.assert_called_with(state="disabled")
        app.start_button.configure.assert_called_with(state="normal")
        self.assertTrue(any("无法确认官方开放状态" in str(c)
                            for c in app.status.set.call_args_list))

    def test_tray_quit_waits_for_scheduled_worker_cleanup(self):
        app = stub()
        app.scheduled_worker = Mock()
        Application.quit_application(app)
        app.scheduled_worker.stop.assert_called_once()
        app.destroy.assert_not_called()
        self.assertTrue(app._closing)
        app.events.put(MonitorEvent("scheduled_finished"))
        Application.poll(app)
        app.destroy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
