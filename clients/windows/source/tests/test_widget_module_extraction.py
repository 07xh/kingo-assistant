"""Read-only Tk component functions are no longer owned by the main UI module."""
import inspect
from pathlib import Path
from unittest import TestCase

import windows_gui
import windows_readonly_widgets


class WidgetModuleExtractionTests(TestCase):
    def test_gui_reexports_shared_helpers_for_existing_callers(self):
        self.assertIs(
            windows_gui._open_readonly_popup,
            windows_readonly_widgets._open_readonly_popup)
        self.assertIs(
            windows_gui._create_readonly_tree,
            windows_readonly_widgets._create_readonly_tree)

    def test_helpers_lived_in_dedicated_ui_only_source_file(self):
        for helper in (
            windows_gui._open_readonly_popup,
            windows_gui._create_readonly_tree,
        ):
            self.assertEqual(
                Path(inspect.getsourcefile(helper)).name,
                "windows_readonly_widgets.py",
            )
        text = Path(windows_readonly_widgets.__file__).read_text(
            encoding="utf-8")
        self.assertNotIn("httpx", text)
        self.assertNotIn("session_cookie", text)
        self.assertNotIn("Kingosoft", text)


if __name__ == "__main__":
    import unittest
    unittest.main()
