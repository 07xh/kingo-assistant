"""Offline lifecycle tests: window close must not silently cancel work."""
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from desktop_tray import TrayController
from windows_gui import Application


class TrayLifecycleTests(TestCase):
    @staticmethod
    def app(*, tray_ready: bool, has_worker: bool = True):
        tray = Mock()
        tray.start.return_value = tray_ready
        worker = Mock() if has_worker else None
        appointment = object()
        return SimpleNamespace(
            _closing=False,
            _tray=tray,
            worker=worker,
            _official_portal_appointment=appointment,
            withdraw=Mock(),
            iconify=Mock(),
            deiconify=Mock(),
            lift=Mock(),
            destroy=Mock(),
            status=Mock(),
        )

    def test_close_to_tray_preserves_worker_and_appointment(self):
        app = self.app(tray_ready=True)
        original = app._official_portal_appointment
        Application.close(app)
        app.withdraw.assert_called_once()
        app.iconify.assert_not_called()
        app.worker.stop.assert_not_called()
        self.assertIs(app._official_portal_appointment, original)
        self.assertFalse(app._closing)

    def test_tray_unavailable_keeps_window_in_taskbar(self):
        app = self.app(tray_ready=False)
        Application.close(app)
        app.iconify.assert_called_once()
        app.withdraw.assert_not_called()
        app.worker.stop.assert_not_called()

    def test_explicit_quit_stops_worker_and_clears_appointment(self):
        app = self.app(tray_ready=True)
        Application.quit_application(app)
        self.assertTrue(app._closing)
        self.assertIsNone(app._official_portal_appointment)
        app._tray.stop.assert_called_once()
        app.worker.stop.assert_called_once()
        app.destroy.assert_not_called()

    def test_explicit_quit_without_worker_exits(self):
        app = self.app(tray_ready=True, has_worker=False)
        Application.quit_application(app)
        app.destroy.assert_called_once()

    def test_repeated_exit_is_idempotent(self):
        app = self.app(tray_ready=True)
        Application.quit_application(app)
        Application.quit_application(app)
        app.worker.stop.assert_called_once()

    def test_restore_raises_window(self):
        app = self.app(tray_ready=True)
        Application.restore_window(app)
        app.deiconify.assert_called_once()
        app.lift.assert_called_once()

    def test_non_windows_tray_does_not_start(self):
        backend = TrayController(Mock())
        with patch("desktop_tray.sys.platform", "linux"):
            self.assertFalse(backend.start())
        self.assertIsNone(backend._icon)


if __name__ == "__main__":
    import unittest
    unittest.main()
