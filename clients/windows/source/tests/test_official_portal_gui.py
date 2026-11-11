"""Headless tests for the official-browser one-shot fallback in Windows GUI."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from core.official_portal_schedule import CHINA_TIMEZONE
from windows_gui import Application


class OfficialPortalGuiTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 16, 0, tzinfo=CHINA_TIMEZONE).timestamp()
        self.plan_at = datetime(2026, 12, 21, 9, 0, tzinfo=CHINA_TIMEZONE).timestamp()
        self.config = {
            "strategy": {"target_time": "2026-12-21 09:00:00"},
            "courses": [{"key": "A", "course_id": "SYNTHETIC-101"}],
        }
        self.app = SimpleNamespace(
            _official_portal_appointment=None,
            _closing=False,
            portal_appointment_button=Mock(),
            portal_appointment_status=Mock(),
            path=SimpleNamespace(get=lambda: str(Path("config.yaml"))),
            log=Mock(),
            open_official_portal=Mock(),
            bell=Mock(),
        )

    def _schedule(self):
        with patch("windows_gui.read_config", return_value=self.config), \
             patch("windows_gui.time.time", return_value=self.now), \
             patch("windows_gui.messagebox.askyesno", return_value=True) as confirmation:
            Application.toggle_portal_appointment(self.app)
        confirmation.assert_called_once()
        self.assertIsNotNone(self.app._official_portal_appointment)
        self.app.open_official_portal.assert_not_called()

    def test_only_one_launch_at_confirmed_time_and_never_submit(self):
        self._schedule()
        with patch("windows_gui.time.time", return_value=self.plan_at - 30):
            Application._tick_portal_appointment(self.app)
        self.app.open_official_portal.assert_not_called()
        with patch("windows_gui.time.time", return_value=self.plan_at):
            Application._tick_portal_appointment(self.app)
            Application._tick_portal_appointment(self.app)
        self.assertIsNone(self.app._official_portal_appointment)
        self.app.open_official_portal.assert_called_once()
        self.app.bell.assert_called_once()

    def test_cancel_prevents_opening_at_target_time(self):
        self._schedule()
        Application.toggle_portal_appointment(self.app)
        self.assertIsNone(self.app._official_portal_appointment)
        with patch("windows_gui.time.time", return_value=self.plan_at + 1):
            Application._tick_portal_appointment(self.app)
        self.app.open_official_portal.assert_not_called()

    def test_without_user_confirmation_never_schedules(self):
        with patch("windows_gui.read_config", return_value=self.config), \
             patch("windows_gui.time.time", return_value=self.now), \
             patch("windows_gui.messagebox.askyesno", return_value=False):
            Application.toggle_portal_appointment(self.app)
        self.assertIsNone(self.app._official_portal_appointment)

    def test_template_course_is_blocked_before_any_launch(self):
        config = {**self.config, "courses": [{"key": "A", "course_id": "SECOND_OPTION_ID"}]}
        with patch("windows_gui.read_config", return_value=config), \
             patch("windows_gui.time.time", return_value=self.now), \
             patch("windows_gui.messagebox.showwarning") as warning:
            Application.toggle_portal_appointment(self.app)
        warning.assert_called_once()
        self.assertIsNone(self.app._official_portal_appointment)

    def test_when_gui_is_closing_no_launch_occurs(self):
        self._schedule()
        self.app._closing = True
        with patch("windows_gui.time.time", return_value=self.plan_at):
            Application._tick_portal_appointment(self.app)
        self.app.open_official_portal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
