"""Regression: legacy manual-browser fallback is absent from the API-only Windows app."""
import unittest

import windows_gui
from windows_gui import Application


class APIOnlyClientTests(unittest.TestCase):
    def test_no_browser_module_or_manual_launch_callbacks(self):
        self.assertFalse(hasattr(windows_gui, "webbrowser"))
        self.assertFalse(hasattr(windows_gui, "OFFICIAL_PORTAL"))
        for name in (
            "toggle_portal_appointment", "_tick_portal_appointment",
            "open_official_portal", "prepare_selected_course",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(Application, name))


if __name__ == "__main__":
    unittest.main()
