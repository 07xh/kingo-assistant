"""Tk plan popup includes a non-mutating match of school and local targets."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.ncwu_course_plan import PlanSnapshot
from windows_gui import Application


class PlanPopupTargetReviewTest(TestCase):
    def test_popup_uses_local_config_and_does_not_modify_it(self):
        app = SimpleNamespace(path=SimpleNamespace(get=lambda: "config.yaml"))
        source = PlanSnapshot("read", (), 0, "table", "2026", "0")
        config = {"xnxq": "2026-2027-1", "courses": [{
            "key": "A", "course_id": "C01", "class_id": "CLASS01",
        }]}
        with patch("windows_gui.read_config", return_value=config) as load, \
             patch("windows_gui.tk.Toplevel") as win, \
             patch("windows_gui.ttk.Label") as label, \
             patch("windows_gui.ttk.Frame") as frame, \
             patch("windows_gui.ttk.Button") as button, \
             patch("windows_gui.ttk.Treeview") as tree, \
             patch("windows_gui.ttk.Scrollbar") as scrollbar:
            Application.show_course_plan_rows(app, source)
        load.assert_called_once()
        self.assertEqual(config["courses"][0]["course_id"], "C01")
        button.assert_called_once()
        self.assertIn("查看逐项志愿核对", str(button.call_args))
        self.assertIn("开课计划", str(label.call_args_list))
        win.assert_called_once()
        tree.assert_called_once()
        scrollbar.assert_called_once()

    def test_unreadable_local_config_does_not_hide_school_plan(self):
        app = SimpleNamespace(path=SimpleNamespace(get=lambda: "config.yaml"))
        source = PlanSnapshot("read", (), 0, "table", "2026", "0")
        with patch("windows_gui.read_config", side_effect=ValueError("BAD")), \
             patch("windows_gui.tk.Toplevel"), \
             patch("windows_gui.ttk.Label") as label, \
             patch("windows_gui.ttk.Frame"), \
             patch("windows_gui.ttk.Button") as button, \
             patch("windows_gui.ttk.Treeview") as tree, \
             patch("windows_gui.ttk.Scrollbar"):
            Application.show_course_plan_rows(app, source)
        button.assert_not_called()
        tree.assert_called_once()
        self.assertTrue(any(
            "本地课程配置不可读取" in str(c)
            for c in label.call_args_list
        ))


if __name__ == "__main__":
    import unittest
    unittest.main()
