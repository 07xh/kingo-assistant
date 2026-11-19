"""Dense Home page keeps every control, callback and status on Application.

Layout-only contract: the builder must create one group per concern, bind
each button to the existing owner method without invoking it, and never touch
the school API, the worker or the configuration writer.
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

    def _render(self):
        def make_button(parent, **kwargs):
            control = Mock(name=kwargs["text"])
            self.buttons[kwargs["text"]] = (control, kwargs)
            return control

        self.groups = []

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
             patch("windows_page_sections.tk.Frame"), \
             patch("windows_page_sections.tk.StringVar") as variable:
            build_home_page(self.app)
            return frame, label, tree, scrollbar, variable

    def test_one_group_per_concern_with_the_grid_in_the_middle(self):
        _, _, tree, _, variable = self._render()
        self.assertEqual(
            [group[2]["text"].strip() for group in self.groups],
            [
                "⑤ 自动提交与验收证据",
                "① 登录与账号（协议已接入 · 待实机验收）",
                "② 目标课程与方案（只写本机配置）",
                "③ 课程余量查询（只读 · 不提交）",
                "④ 目标课程列表 · 本地配置 / 只读状态",
            ],
        )
        self.assertIs(self.app.course_frame, self.groups[4][0])
        self.assertIs(self.app.table, tree.return_value)
        self.assertIs(self.app.enrollment_gate_status, variable.return_value)
        variable.assert_called_once_with(
            value="自动选课：未接入提交适配器（验收证据 0/4）")
        self.app.enrollment_gate_label.assert_called_once()

    def test_grid_specification_matches_the_dense_shell(self):
        _, _, tree, scrollbar, _ = self._render()
        self.assertEqual(tree.call_args.kwargs["columns"],
                         ("key", "course", "class_id", "teacher", "status"))
        self.assertEqual(tree.call_args.kwargs["show"], "headings")
        # A short requested height lets the packer expand it to fill the page.
        self.assertEqual(tree.call_args.kwargs["height"], 6)
        self.assertEqual(tree.call_args.kwargs["selectmode"], "browse")
        self.assertEqual(
            [call.kwargs["text"]
             for call in tree.return_value.heading.call_args_list],
            ["志愿", "课程 / 代码", "教学班", "任课教师", "查询结果"])
        self.assertEqual(
            [call.kwargs["width"]
             for call in tree.return_value.column.call_args_list],
            [62, 240, 110, 110, 320])
        self.assertEqual(
            [call.kwargs["stretch"] is True
             for call in tree.return_value.column.call_args_list],
            [False, True, False, False, True])
        scrollbar.assert_called_once()
        scrollbar.return_value.pack.assert_called_once_with(
            side="right", fill="y")

    def test_every_home_button_uses_owner_callback_without_invoking_it(self):
        self._render()
        bindings = {
            "填写 / 修改目标课程": "edit_settings",
            "管理课程方案": "manage_plans",
            "登录本人教务账号": "start_native_login",
            "检查登录与课程数据（只读）": "check_native_preparation",
            "清除登录状态": "clear_native_session",
            "停止查询": "stop_monitor",
            "检查是否具备运行条件": "preflight",
            "读取本人课表（既有接口 · 只读）": "check_known_timetable",
            "实时读取可选课程（S202 · 只读）": "check_live_catalog",
            "开始自动刷新（≥60秒）": "toggle_live_polling",
            "开始查询余量": "start_monitor",
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
        for name in ("自动提交（暂不可用）", "取消自动计划"):
            self.assertEqual(self.buttons[name][1]["state"], "disabled")
        # 停止查询 is created armed and disabled right after binding.
        self.assertNotIn("state", self.buttons["停止查询"][1])
        self.buttons["停止查询"][0].configure.assert_called_once_with(
            state="disabled")
        self.assertIs(self.app.native_login_button,
                      self.buttons["登录本人教务账号"][0])
        self.assertIs(self.app.live_catalog_button,
                      self.buttons["实时读取可选课程（S202 · 只读）"][0])
        self.assertIs(self.app.live_poll_button,
                      self.buttons["开始自动刷新（≥60秒）"][0])
        self.assertIs(self.app.start_button, self.buttons["开始查询余量"][0])
        self.assertEqual(self.buttons["开始查询余量"][1]["style"],
                         "KingoPrimary.TButton")
        self.assertIs(self.app.scheduled_start_button,
                      self.buttons["自动提交（暂不可用）"][0])
        self.assertIs(self.app.scheduled_stop_button,
                      self.buttons["取消自动计划"][0])

    def test_home_statuses_still_use_existing_tk_variables_and_copy_shortcut(self):
        _, label, tree, _, _ = self._render()
        used_vars = [call.kwargs.get("textvariable")
                     for call in label.call_args_list]
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
            "待实机验收" in str(call) for call in labels.call_args_list))
        self.assertTrue(callable(Application._build))


if __name__ == "__main__":
    import unittest
    unittest.main()
