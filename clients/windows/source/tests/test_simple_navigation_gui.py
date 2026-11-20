"""Offline tests for beginner-facing tab navigation and truthful statuses."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application


class SimpleNavigationTests(TestCase):
    def test_browser_backup_navigation_is_removed(self):
        self.assertFalse(hasattr(Application, "toggle_portal_panel"))
        self.assertFalse(hasattr(Application, "open_official_portal"))

    def test_advanced_configuration_opens_tool_tab(self):
        tools = object()
        frame = Mock()
        frame.winfo_manager.return_value = ""
        app = SimpleNamespace(
            notebook=Mock(), tab_tools=tools,
            advanced_panel=frame, tools_operations=Mock(),
            after_idle=Mock(), tools_scroll_canvas=Mock(),
        )
        Application.toggle_advanced_panel(app)
        app.notebook.select.assert_called_once_with(tools)
        frame.pack.assert_called_once()
        app.after_idle.assert_called_once_with(app.tools_scroll_canvas.yview_moveto, 1)

    def test_function_guide_describes_each_capability_without_actions(self):
        with patch("windows_gui.messagebox.showinfo") as message:
            Application.show_function_guide(SimpleNamespace())
        message.assert_called_once()
        displayed = str(message.call_args)
        self.assertIn("自动提交", displayed)
        self.assertIn("禁用", displayed)
        self.assertIn("只读取", displayed)
        self.assertIn("纯 HTTP", displayed)
        self.assertIn("接口", displayed)

    def test_template_course_is_marked_as_example_not_online_result(self):
        table = Mock()
        table.get_children.return_value = []
        app = SimpleNamespace(table=table, _display_courses={})
        config = {"courses": [
            {"key": "A", "course_id": "FILL_FROM_OFFICIAL_PAGE"},
            {"key": "B", "course_id": "MATH101", "course_name": "数学"},
        ]}
        Application.populate(app, config)
        rows = [call.kwargs["values"] for call in table.insert.call_args_list]
        self.assertEqual(rows[0][-1], "仅示例 · 未连接")
        self.assertEqual(rows[1][-1], "未查询")
        self.assertNotIn("已选成功", str(rows))


if __name__ == "__main__":
    import unittest
    unittest.main()
