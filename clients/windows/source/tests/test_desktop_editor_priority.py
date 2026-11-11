"""Tests for the course-priority ordering controls without a Tk display."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from desktop_editor import SettingsEditor


class PriorityOrderEditorTest(unittest.TestCase):
    def make_editor(self, selected=1):
        courses = [
            {"key": "A", "course_id": "C1", "class_id": "K1", "group": "G"},
            {"key": "B", "course_id": "C2", "class_id": "K2", "group": "G"},
            {"key": "C", "course_id": "C3", "class_id": "K3", "group": ""},
        ]
        return SimpleNamespace(
            courses=courses,
            selection=lambda: selected,
            table=Mock(),
            refresh_table=Mock(),
            bell=Mock(),
        )

    def test_move_up_changes_execution_priority_and_preserves_group(self):
        app = self.make_editor(selected=1)
        SettingsEditor.move_course(app, -1)
        self.assertEqual([x["key"] for x in app.courses], ["B", "A", "C"])
        self.assertEqual(app.courses[0]["group"], "G")
        self.assertEqual(app.courses[1]["group"], "G")
        app.refresh_table.assert_called_once()
        app.table.selection_set.assert_called_once_with("0")
        app.table.focus.assert_called_once_with("0")
        app.table.see.assert_called_once_with("0")

    def test_move_down_changes_priority(self):
        app = self.make_editor(selected=0)
        SettingsEditor.move_course(app, 1)
        self.assertEqual([x["key"] for x in app.courses], ["B", "A", "C"])
        app.table.selection_set.assert_called_once_with("1")

    def test_move_at_boundary_does_not_change_order(self):
        app = self.make_editor(selected=0)
        SettingsEditor.move_course(app, -1)
        self.assertEqual([x["key"] for x in app.courses], ["A", "B", "C"])
        app.refresh_table.assert_not_called()
        app.bell.assert_called_once()

    def test_invalid_move_direction_is_rejected(self):
        app = self.make_editor(selected=1)
        with self.assertRaises(ValueError):
            SettingsEditor.move_course(app, 2)

    def test_missing_selection_is_explained(self):
        app = self.make_editor(selected=None)
        with patch("desktop_editor.messagebox.showinfo") as dialog:
            SettingsEditor.move_course(app, -1)
        dialog.assert_called_once()
        self.assertEqual([x["key"] for x in app.courses], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
