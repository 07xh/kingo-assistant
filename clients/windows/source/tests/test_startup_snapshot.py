"""Offline regression: one coherent config snapshot, no redundant confirmation."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.preflight import Check
from windows_gui import Application


def stub():
    app = SimpleNamespace(
        worker=None,
        _benchmark_in_progress=False,
        path=SimpleNamespace(get=lambda: "example.yaml"),
        status=Mock(),
        log=Mock(),
        populate=Mock(),
        events=object(),
    )
    for name in ("start_button", "edit_button", "plans_button",
                 "config_entry", "pick_button", "open_button", "stop_button"):
        setattr(app, name, Mock())
    return app


class StartupSnapshotTests(TestCase):
    def test_explicit_start_reads_one_snapshot_and_validates_live_once(self):
        app = stub()
        config = {"courses": [{"key": "A", "course_id": "SYNTHETIC"}]}
        with patch("windows_gui.read_config", return_value=config) as read, \
             patch("windows_gui.resolve_state_file", return_value=config) as resolve, \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("本地", True, "ok")]), \
             patch("windows_gui.validate") as validate, \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.askokcancel") as extra_confirm:
            Application.start_monitor(app)
        read.assert_called_once_with(Path("example.yaml"), offline=True)
        resolve.assert_called_once()
        validate.assert_called_once_with(config, check_only=False)
        worker.assert_called_once_with(config, app.events)
        worker.return_value.start.assert_called_once()
        extra_confirm.assert_not_called()

    def test_advisory_warning_does_not_force_extra_modal(self):
        app = stub()
        config = {"courses": [{"key": "A", "course_id": "SYNTHETIC"}]}
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.resolve_state_file", return_value=config), \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("本地录入", False, "仅提示", "warning")]), \
             patch("windows_gui.validate"), \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.askokcancel") as confirm:
            Application.start_monitor(app)
        confirm.assert_not_called()
        worker.return_value.start.assert_called_once()
        self.assertTrue(any("1 项非阻断提示" in str(call)
                            for call in app.log.call_args_list))

    def test_hard_blocker_never_starts_client(self):
        app = stub()
        with patch("windows_gui.read_config", return_value={}) as read, \
             patch("windows_gui.resolve_state_file", return_value={}), \
             patch("windows_gui.inspect_readiness",
                   return_value=[Check("本人会话", False, "缺少官方会话")]), \
             patch("windows_gui.validate") as validate, \
             patch("windows_gui.MonitorWorker") as worker, \
             patch("windows_gui.messagebox.showwarning") as warning:
            Application.start_monitor(app)
        read.assert_called_once()
        warning.assert_called_once()
        validate.assert_not_called()
        worker.assert_not_called()
