"""Headless checks for Windows scheduled-runner admission UI."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from windows_gui import Application


class ScheduledUiAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None,
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            status=Mock(),
            log=Mock(),
        )
        self.plan = SimpleNamespace(
            target_time="2026-12-21 09:00:00",
            targets=[1, 2],
            poll_interval_range=(60, 120),
            max_wait_minutes=480,
        )

    def test_local_preflight_never_starts_school_client(self):
        with patch("windows_gui.read_config", return_value={"local": True}) as read, \
             patch("windows_gui.prepare_scheduled_plan", return_value=self.plan) as prepare, \
             patch("windows_gui.KingoClient", side_effect=AssertionError("network")), \
             patch("windows_gui.messagebox.showinfo") as dialog:
            Application.inspect_scheduled_runner(self.app)
        read.assert_called_once()
        self.assertTrue(read.call_args.kwargs["offline"])
        prepare.assert_called_once()
        dialog.assert_called_once()
        self.assertIn("真实自动选课未启用", str(self.app.status.set.call_args))
        self.assertIn("不是抢课启动", str(dialog.call_args))

    def test_incomplete_plan_is_reported_without_scheduling(self):
        with patch("windows_gui.read_config", return_value={}), \
             patch("windows_gui.prepare_scheduled_plan", side_effect=ValueError("教学班缺失")), \
             patch("windows_gui.messagebox.showwarning") as warning:
            Application.inspect_scheduled_runner(self.app)
        warning.assert_called_once()
        self.assertIn("教学班缺失", str(warning.call_args))

    def test_active_monitor_blocks_preflight(self):
        self.app.worker = object()
        with patch("windows_gui.read_config", side_effect=AssertionError("must not read")), \
             patch("windows_gui.messagebox.showinfo") as info:
            Application.inspect_scheduled_runner(self.app)
        info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
