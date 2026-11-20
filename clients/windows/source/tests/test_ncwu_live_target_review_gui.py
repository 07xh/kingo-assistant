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
            _live_catalog_tree=Mock(),
            _live_target_review_button=Mock(),
            _live_target_report=None, catalog_count=Mock(),
            course_filter=Mock(), teacher_filter=Mock(),
            add_catalog_course_button=Mock(), edit_settings=Mock(),
            path=SimpleNamespace(get=lambda: "config.yaml"),
        )
        self.app._live_catalog_tree.get_children.return_value = ()
        self.app._live_catalog_tree.selection.return_value = ()
        self.app.course_filter.get.return_value = ""
        self.app.teacher_filter.get.return_value = ""

    def test_course_candidate_inserts_display_row_but_never_class_identity(self):
        with patch("windows_gui.read_config", return_value=cfg()), \
             patch("windows_gui.tk.Toplevel") as popup:
            Application.show_live_catalog_rows(self.app, live())
        popup.assert_not_called()
        self.assertEqual(self.app._live_target_report.candidate_targets, 1)
        self.assertEqual(self.app._live_target_report.targets[0].matching_rows, (0,))
        tree = self.app._live_catalog_tree
        self.assertEqual(tree.insert.call_count, 1)
        values = tree.insert.call_args.kwargs["values"]
        self.assertIn("课程候选：A", values)
        self.assertIn("DISPLAY_SKBH", values)
        self.assertNotIn("REAL_INTERNAL_SKBJDM", values)
        self.assertNotIn("已选成功", str(values))

    def test_invalid_local_config_does_not_hide_live_catalog(self):
        with patch("windows_gui.read_config", side_effect=ValueError("BAD")):
            Application.show_live_catalog_rows(self.app, live())
        self.assertIsNone(self.app._live_target_report)
        self.app._live_target_review_button.configure.assert_called_once_with(state="disabled")
        self.app._live_catalog_tree.insert.assert_called_once()

    def test_course_filter_and_target_editor_use_only_display_metadata(self):
        tree = self.app._live_catalog_tree
        self.app.teacher_filter.get.return_value = "李老师"
        with patch("windows_gui.read_config", return_value=cfg()):
            Application.show_live_catalog_rows(self.app, live())
        tree.insert.assert_not_called()
        self.app.catalog_count.set.assert_called_once_with("显示 0 / 1 门")
        self.app.teacher_filter.get.return_value = "张"
        self.app.course_filter.get.return_value = "001234"
        Application.render_catalog_rows(self.app)
        tree.insert.assert_called_once()
        tree.selection.return_value = ("0",)
        Application.add_catalog_course(self.app)
        self.app.edit_settings.assert_called_once_with(course={
            "course_id": "001234", "course_name": "工程制图", "teacher": "张老师",
        })

    def test_review_message_requires_a_real_report(self):
        with patch("windows_gui.messagebox.showinfo") as dialog:
            Application.show_live_target_review(self.app)
        dialog.assert_called_once()
        self.assertIn("没有可核对", str(dialog.call_args))


if __name__ == "__main__":
    import unittest
    unittest.main()
