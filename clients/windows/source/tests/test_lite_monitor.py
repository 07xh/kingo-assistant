"""Unit tests for the original monitor lifecycle reused by Lite.

No school requests are permitted: worker and validator are fakes.
"""
from __future__ import annotations

from pathlib import Path
import queue
import sys
import types
from unittest import TestCase
from unittest.mock import patch

from lite_monitor import LiteReadMonitor


class FakeWorker:
    def __init__(self, config, events, *, session_cookie):
        self.config = config
        self.events = events
        self.session_cookie = session_cookie
        self.thread = None
        self.stopped = False

    def start(self):
        self.thread = self
        self._alive = True

    def is_alive(self):
        return self._alive

    def stop(self):
        self.stopped = True
        self._alive = False
        self.events.put(types.SimpleNamespace(kind="finished", value="stopped"))


class LiteMonitorTests(TestCase):
    def test_no_login_does_not_import_or_run_monitor(self):
        monitor = LiteReadMonitor()
        response = monitor.start({}, session_cookie="", origin="")
        self.assertEqual(response["code"], "missing_session")
        self.assertFalse(response["active"])

    def test_only_existing_validated_worker_can_start(self):
        fake_gui = types.ModuleType("windows_gui")
        fake_gui.MonitorWorker = FakeWorker
        fake_gui.app_dir = lambda: Path(".")
        monitor = LiteReadMonitor()
        with patch.dict(sys.modules, {"windows_gui": fake_gui}), \
             patch("core.desktop_settings.resolve_state_file", side_effect=lambda config, _: config), \
             patch("main.validate") as validate:
            result = monitor.start(
                {"strategy": {"dry_run": True}},
                session_cookie="JSESSIONID=private",
                origin="jwmis.ncwu.edu.cn"
            )
        validate.assert_called_once()
        self.assertEqual(result["code"], "started")
        self.assertTrue(result["active"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["enrollment_available"])
        self.assertNotIn("private", str(result))
        self.assertEqual(monitor.worker.session_cookie, "JSESSIONID=private")
        stopped = monitor.stop()
        self.assertTrue(monitor.worker.stopped)
        self.assertIn(stopped["phase"], ("stopping", "stopped"))
        self.assertEqual(monitor.status()["phase"], "stopped")

    def test_original_preflight_rejection_never_starts_worker(self):
        fake_gui = types.ModuleType("windows_gui")
        fake_gui.MonitorWorker = FakeWorker
        fake_gui.app_dir = lambda: Path(".")
        monitor = LiteReadMonitor()
        with patch.dict(sys.modules, {"windows_gui": fake_gui}), \
             patch("core.desktop_settings.resolve_state_file", side_effect=lambda config, _: config), \
             patch("main.validate", side_effect=ValueError("policy missing")):
            result = monitor.start({}, session_cookie="JSESSIONID=private",
                                   origin="jwmis.ncwu.edu.cn")
        self.assertEqual(result["code"], "preflight_rejected")
        self.assertFalse(result["active"])

    def test_monitor_status_does_not_echo_unapproved_messages_or_states(self):
        monitor = LiteReadMonitor()
        monitor.updates.put(types.SimpleNamespace(kind="message", value="Cookie=secret"))
        monitor.updates.put(types.SimpleNamespace(
            kind="status", value=(2, {"secret": "session-token"}, 12.0, 4.0)
        ))
        result = monitor.status()
        self.assertEqual(result["cycles"], 2)
        self.assertEqual(result["last_ms"], 12.0)
        self.assertNotIn("secret", str(result))
        self.assertNotIn("session-token", str(result))
        self.assertEqual(result["phase"], "idle")

    def test_broken_state_file_is_rejected(self):
        fake_gui = types.ModuleType("windows_gui")
        fake_gui.MonitorWorker = FakeWorker
        fake_gui.app_dir = lambda: Path(".")
        monitor = LiteReadMonitor()
        with patch.dict(sys.modules, {"windows_gui": fake_gui}), \
             patch("core.desktop_settings.resolve_state_file", side_effect=ValueError("bad path")):
            result = monitor.start({}, session_cookie="JSESSIONID=private",
                                   origin="jwmis.ncwu.edu.cn")
        self.assertEqual(result["code"], "preflight_rejected")
        self.assertFalse(result["active"])
