"""Headless GUI wiring tests for the explicit native API preparation action."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application


class NativePreparationGuiTest(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None,
            scheduled_worker=None,
            _session_check_in_progress=False,
            _native_preparation_in_progress=False,
            _closing=False,
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            native_prepare_button=Mock(),
            native_preparation_status=Mock(),
            status=Mock(),
            events=queue.Queue(),
        )

    def test_only_explicit_button_launches_native_worker(self):
        config = {"protocol": {"verified": False}, "auth": {"cookie": ""}}
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_native_preparation(self.app)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"], "KingoNativeApiPreparation")
            thread.call_args.kwargs["target"]()
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "native_preparation")
        self.assertEqual(event.value.code, "protocol_unverified")
        self.app.native_prepare_button.configure.assert_called_with(state="disabled")

    def test_running_scheduled_task_blocks_preparation(self):
        self.app.scheduled_worker = object()
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_native_preparation(self.app)
        thread.assert_not_called()

    def test_closing_app_blocks_preparation(self):
        self.app._closing = True
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_native_preparation(self.app)
        thread.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
