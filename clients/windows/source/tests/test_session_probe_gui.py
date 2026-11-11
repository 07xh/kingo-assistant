"""Headless Windows GUI tests for script-session readiness (offline only)."""
import queue
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application


class SessionProbeGuiTest(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None,
            _session_check_in_progress=False,
            _closing=False,
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            session_check_button=Mock(),
            status=Mock(),
            events=queue.Queue(),
        )

    def test_explicit_probe_uses_worker_and_unverified_config_never_networks(self):
        config = {"protocol": {"verified": False}, "auth": {"cookie": ""}}
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_script_session(self.app)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"], "KingoSessionProbe")
            self.assertTrue(self.app._session_check_in_progress)
            self.app.session_check_button.configure.assert_called_with(state="disabled")
            # Execute the posted worker synchronously: no networking is possible
            # because the official identity protocol remains unverified.
            thread.call_args.kwargs["target"]()
        event = self.app.events.get_nowait()
        self.assertEqual(event.kind, "session_probe")
        self.assertEqual(event.value.code, "protocol_unverified")
        self.assertFalse(event.value.verified)

    def test_closing_gui_does_not_launch_probe(self):
        self.app._closing = True
        with patch("windows_gui.threading.Thread") as thread:
            Application.check_script_session(self.app)
        thread.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
