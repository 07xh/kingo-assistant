"""Headless live S202 GUI target linkage: no writes or class-ID claims."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.ncwu_live_elective import LiveElectiveCourse, LiveElectiveSnapshot
from windows_gui import Application


def live():
    row = LiveElectiveCourse(
        "工程制图", "001234", "DISPLAY_SKBH",
        "张老师", "未知", "0",
    )
    return LiveElectiveSnapshot(
        "catalog_read", "catalog", "reported_open",
        (row,), 17, academic_year="2026", academic_term="0",
    )


def cfg():
    return {"xnxq": "2026-2027-1", "courses": [{
        "key": "A", "course_id": "001234",
        "class_id": "REAL_INTERNAL_SKBJDM",
        "course_name": "工程制图", "teacher": "张老师",
    }]}


class S202LiveTargetGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            _live_catalog_popup=None,
            _live_catalog_tree=None,
            _live_catalog_message=None,
            _live_target_message=None,
            _live_target_review_button=None,
            _live_target_report=None,
            show_live_target_review=Mock(),
            path=SimpleNamespace(get=lambda: "config.yaml"),
        )

    def test_course_candidate_inserts_display_row_but_never_class_identity(self):
        with patch("windows_gui.read_config", return_value=cfg()), \
             patch("windows_gui.tk.Toplevel") as popup, \
             patch("windows_gui.tk.StringVar"), \
             patch("windows_gui.ttk.Label"), \
             patch("windows_gui.ttk.Frame"), \
             patch("windows_gui.ttk.Button") as button, \
             patch("windows_gui.ttk.Treeview") as tree, \
             patch("windows_gui.ttk.Scrollbar"):
            tree.return_value.get_children.return_value = []
            Application.show_live_catalog_rows(self.app, live())
        popup.assert_called_once()
        button.assert_called_once()
        self.assertEqual(self.app._live_target_report.candidate_targets, 1)
        self.assertEqual(self.app._live_target_report.targets[0].matching_rows, (0,))
        columns = tree.call_args.kwargs["columns"]
        self.assertIn("target", columns)
        self.assertEqual(tree.return_value.insert.call_count, 1)
        values = tree.return_value.insert.call_args.kwargs["values"]
        self.assertIn("课程候选：A", values)
        self.assertIn("DISPLAY_SKBH", values)
        self.assertNotIn("REAL_INTERNAL_SKBJDM", values)
        self.assertNotIn("已选成功", str(values))

    def test_invalid_local_config_does_not_hide_live_catalog(self):
        with patch("windows_gui.read_config", side_effect=ValueError("BAD")), \
             patch("windows_gui.tk.Toplevel"), \
             patch("windows_gui.tk.StringVar"), \
             patch("windows_gui.ttk.Label"), \
             patch("windows_gui.ttk.Frame"), \
             patch("windows_gui.ttk.Button") as button, \
             patch("windows_gui.ttk.Treeview") as tree, \
             patch("windows_gui.ttk.Scrollbar"):
            tree.return_value.get_children.return_value = []
            Application.show_live_catalog_rows(self.app, live())
        self.assertIsNone(self.app._live_target_report)
        self.app._live_target_message.set.assert_called()
        button.return_value.configure.assert_called_once_with(state="disabled")
        tree.return_value.insert.assert_called_once()

    def test_review_message_requires_a_real_report(self):
        with patch("windows_gui.messagebox.showinfo") as dialog:
            Application.show_live_target_review(self.app)
        dialog.assert_called_once()
        self.assertIn("没有可核对", str(dialog.call_args))


if __name__ == "__main__":
    import unittest
    unittest.main()
