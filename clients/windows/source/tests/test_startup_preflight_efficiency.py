"""Headless regression tests for single-snapshot, offline-first GUI startup."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from core.preflight import Check
from windows_gui import Application


def app_stub():
    buttons = ("start_button", "edit_button", "plans_button",
               "config_entry", "pick_button", "open_button", "stop_button")
    app = SimpleNamespace(
        worker=None,
        _benchmark_in_progress=False,
        path=SimpleNamespace(get=lambda: "example.yaml"),
        status=Mock(),
        log=Mock(),
        populate=Mock(),
        events=object(),
    )
    for name in buttons:
        setattr(app, name, Mock())
    return app


class StartupPreflightEfficiencyTest(unittest.TestCase):
    def test_successful_start_uses_one_config_snapshot_and_live_validation(self):
        app = app_stub()
        config = {"courses": [{"key": "A", "course_id": "SAMPLE"}]}
        with patch("windows_gui.read_config", return_value=config) as read, \
             patch("windows_gui.resolve_state_file", return_value=config) as resolve, \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("本地配置", True, "通过")]), \
             patch("windows_gui.validate") as validate, \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.askokcancel") as ask:
            Application.start_monitor(app)
        read.assert_called_once_with(Path("example.yaml"), offline=True)
        resolve.assert_called_once()
        validate.assert_called_once_with(config, check_only=False)
        worker.assert_called_once_with(config, app.events)
        worker.return_value.start.assert_called_once()
        ask.assert_not_called()

    def test_advisory_manual_timetable_does_not_prevent_read_only_start(self):
        app = app_stub()
        config = {"courses": [{"key": "A", "course_id": "SAMPLE",
                               "time_slots": "invalid"}]}
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.resolve_state_file", return_value=config), \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("手动节次格式", False,
                                       "仅影响本地冲突初筛", "warning")]), \
             patch("windows_gui.validate"), \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.askokcancel") as ask, \
             patch("windows_gui.messagebox.showwarning") as block_dialog:
            Application.start_monitor(app)
        block_dialog.assert_not_called()
        ask.assert_not_called()
        self.assertTrue(any("1 项非阻断提示" in str(call)
                            for call in app.log.call_args_list))
        worker.return_value.start.assert_called_once()

    def test_blockers_skip_live_validation_and_worker(self):
        app = app_stub()
        config = {"courses": []}
        with patch("windows_gui.read_config", return_value=config) as read, \
             patch("windows_gui.resolve_state_file", return_value=config), \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("本人登录会话", False, "缺少会话")]), \
             patch("windows_gui.validate") as validate, \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.showwarning") as block_dialog:
            Application.start_monitor(app)
        read.assert_called_once()
        block_dialog.assert_called_once()
        validate.assert_not_called()
        worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
