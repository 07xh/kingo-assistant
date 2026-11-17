"""The complete Home widget tree keeps its state on Application.

This is a layout-only refactor. Neither the view builder nor calling it
may read the university API, enable unverified enrollment, or create an
independent controller/store.
"""
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application
from windows_page_sections import build_home_page


class HomePageViewTests(TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.tab_home = object()
        self.app.enrollment_gate_label.return_value = (
            "自动选课：未接入提交适配器（验收证据 0/4）")
        self.buttons = {}
        self.groups = []

    def _render(self):
        def make_button(parent, **kwargs):
            b = Mock(name=kwargs["text"])
            self.buttons[kwargs["text"]] = (b, kwargs)
            return b

        def make_group(parent, **kwargs):
            group = Mock(name=kwargs["text"])
            self.groups.append((group, parent, kwargs))
            return group

        with patch("windows_page_sections.ttk.Frame") as frame, \
             patch("windows_page_sections.ttk.Label") as label, \
             patch("windows_page_sections.ttk.LabelFrame",
                   side_effect=make_group), \
             patch("windows_page_sections.ttk.Button",
                   side_effect=make_button), \
             patch("windows_page_sections.ttk.Treeview") as tree, \
             patch("windows_page_sections.ttk.Scrollbar") as scrollbar, \
             patch("windows_page_sections.tk.StringVar") as variable:
            build_home_page(self.app)
            return frame, label, tree, scrollbar, variable

    def test_three_home_steps_are_preserved_in_one_owner(self):
        _, _, tree, scrollbar, variable = self._render()
        self.assertEqual(
            [group[2]["text"] for group in self.groups],
            [
                "第 1 步  ·  课程与志愿（本地可用）",
                "第 2 步  ·  软件内登录（协议已接入 · 待实机验收）",
                "第 3 步  ·  课程余量监测（只读）",
                "目标课程列表 · 本地配置 / 只读状态",
            ],
        )
        self.assertIs(self.app.course_frame, self.groups[3][0])
        self.assertIs(self.app.action_panel, self.app.action_panel)
        self.assertIs(self.app.table, tree.return_value)
        self.assertIs(self.app.enrollment_gate_status, variable.return_value)
        variable.assert_called_once_with(
            value="自动选课：未接入提交适配器（验收证据 0/4）")
        self.app.enrollment_gate_label.assert_called_once()
        self.assertEqual(tree.call_args.kwargs, {
            "columns": ("key", "course", "class_id", "teacher", "status"),
            "show": "headings",
        })
        self.assertIs(tree.call_args.args[0], self.app.course_frame)
        scrollbar.assert_called_once()
        self.assertEqual(
            [call.kwargs["text"] for call in tree.return_value.heading.call_args_list],
            ["志愿", "课程 / 代码", "教学班", "任课教师", "查询结果"],
        )
        self.assertEqual(
            [call.kwargs["width"] for call in tree.return_value.column.call_args_list],
            [70, 225, 110, 120, 240],
        )
        self.assertTrue(all(
            call.kwargs["stretch"] is True
            for call in tree.return_value.column.call_args_list
        ))

    def test_every_home_button_uses_owner_callback_without_invoking_it(self):
        self._render()
        bindings = {
            "填写 / 修改目标课程": "edit_settings",
            "管理课程方案": "manage_plans",
            "在 EXE 登录本人教务账号": "start_native_login",
            "检查登录与课程数据（只读）": "check_native_preparation",
            "清除登录状态": "clear_native_session",
            "读取本人课表（既有接口 · 只读）": "check_known_timetable",
            "实时读取可选课程（S202 · 只读）": "check_live_catalog",
            "开始自动刷新（≥60秒）": "toggle_live_polling",
            "开始查询课程余量": "start_monitor",
            "停止查询": "stop_monitor",
            "检查是否具备运行条件": "preflight",
            "自动提交（暂不可用）": "start_scheduled_enrollment",
            "取消自动计划": "stop_scheduled_enrollment",
        }
        self.assertEqual(set(self.buttons), set(bindings))
        for text, name in bindings.items():
            with self.subTest(text=text):
                widget, kwargs = self.buttons[text]
                self.assertIs(kwargs["command"], getattr(self.app, name))
                getattr(self.app, name).assert_not_called()
                widget.pack.assert_called()
        for name in ("自动提交（暂不可用）", "取消自动计划", "停止查询"):
            self.assertEqual(self.buttons[name][1]["state"], "disabled")
        self.assertIs(self.app.native_login_button, self.buttons["在 EXE 登录本人教务账号"][0])
        self.assertIs(self.app.live_catalog_button, self.buttons["实时读取可选课程（S202 · 只读）"][0])
        self.assertIs(self.app.live_poll_button, self.buttons["开始自动刷新（≥60秒）"][0])
        self.assertIs(self.app.start_button, self.buttons["开始查询课程余量"][0])
        self.assertIs(self.app.scheduled_start_button, self.buttons["自动提交（暂不可用）"][0])
        self.assertIs(self.app.scheduled_stop_button, self.buttons["取消自动计划"][0])

    def test_home_statuses_still_use_existing_tk_variables_and_copy_shortcut(self):
        _, label, tree, _, _ = self._render()
        used_vars = [call.kwargs.get("textvariable") for call in label.call_args_list]
        for name in (
            "native_login_status", "native_preparation_status",
            "timetable_read_status", "live_catalog_status",
            "enrollment_gate_status",
        ):
            with self.subTest(name=name):
                self.assertIn(getattr(self.app, name), used_vars)
        tree.return_value.bind.assert_called_once()
        bind = tree.return_value.bind.call_args
        self.assertEqual(bind.args[0], "<Double-1>")
        bind.args[1](object())
        self.app.copy_selected_field.assert_called_once_with("course_id")

    def test_view_does_not_call_school_or_start_workers(self):
        _, labels, _, _, _ = self._render()
        for name in (
            "start_native_login", "check_native_preparation",
            "start_monitor", "check_live_catalog",
            "start_scheduled_enrollment", "read_config",
        ):
            getattr(self.app, name).assert_not_called()
        self.assertTrue(any(
            "待本人账号验收" in str(c)
            for c in labels.call_args_list))
        self.assertTrue(callable(Application._build))


if __name__ == "__main__":
    import unittest
    unittest.main()
