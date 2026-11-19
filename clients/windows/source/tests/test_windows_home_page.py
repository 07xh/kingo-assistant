"""The compact Bypass-inspired workbench keeps state on Application.

The view is layout-only: it may bind existing callbacks, but it must never
read the university API, enable unverified enrollment, or create a new store.
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

    def test_workbench_groups_share_one_toolbar_and_keep_course_table_central(self):
        _, _, tree, scrollbar, variable = self._render()
        self.assertEqual(
            [group[2]["text"] for group in self.groups],
            [
                "课程方案",
                "账号与数据",
                "监测控制",
                "目标课程列表 · 本地配置 / 只读状态",
            ],
        )
        for index, (group, _parent, _kwargs) in enumerate(self.groups[:3]):
            group.grid.assert_called_once()
            self.assertEqual(group.grid.call_args.kwargs["row"], 0)
            self.assertEqual(group.grid.call_args.kwargs["column"], index)
            self.assertEqual(group.grid.call_args.kwargs["sticky"], "nsew")

        self.assertIs(self.app.course_frame, self.groups[3][0])
        self.assertIs(self.app.table, tree.return_value)
        self.assertIs(self.app.enrollment_gate_status, variable.return_value)
        variable.assert_called_once_with(
            value="自动选课：未接入提交适配器（验收证据 0/4）")
        self.app.enrollment_gate_label.assert_called_once()
        self.assertEqual(tree.call_args.kwargs, {
            "columns": ("key", "course", "class_id", "teacher", "status"),
            "show": "headings",
            "height": 8,
        })
        self.assertIs(tree.call_args.args[0], self.app.course_frame)
        scrollbar.assert_called_once()
        self.assertEqual(
            [call.kwargs["text"] for call in tree.return_value.heading.call_args_list],
            ["志愿", "课程 / 代码", "教学班", "任课教师", "监测状态"],
        )
        self.assertEqual(
            [call.kwargs["width"] for call in tree.return_value.column.call_args_list],
            [70, 225, 110, 120, 240],
        )
        self.assertTrue(all(
            call.kwargs["stretch"] is True
            for call in tree.return_value.column.call_args_list
        ))

    def test_every_workbench_button_uses_original_owner_callback(self):
        self._render()
        bindings = {
            "编辑目标课程": "edit_settings",
            "方案": "manage_plans",
            "登录教务账号": "start_native_login",
            "核验登录与数据": "check_native_preparation",
            "清除登录": "clear_native_session",
            "读取本人课表（既有接口）": "check_known_timetable",
            "读取 S202 目录": "check_live_catalog",
            "自动刷新": "toggle_live_polling",
            "开始只读监测": "start_monitor",
            "停止": "stop_monitor",
            "离线预检": "preflight",
            "提交未接入": "start_scheduled_enrollment",
            "取消": "stop_scheduled_enrollment",
        }
        self.assertEqual(set(self.buttons), set(bindings))
        for text, name in bindings.items():
            with self.subTest(text=text):
                widget, kwargs = self.buttons[text]
                self.assertIs(kwargs["command"], getattr(self.app, name))
                getattr(self.app, name).assert_not_called()
                widget.pack.assert_called()
        for name in ("提交未接入", "取消", "停止"):
            self.assertEqual(self.buttons[name][1]["state"], "disabled")

        self.assertIs(self.app.edit_button, self.buttons["编辑目标课程"][0])
        self.assertIs(self.app.plans_button, self.buttons["方案"][0])
        self.assertIs(self.app.native_login_button, self.buttons["登录教务账号"][0])
        self.assertIs(self.app.native_prepare_button, self.buttons["核验登录与数据"][0])
        self.assertIs(self.app.native_clear_button, self.buttons["清除登录"][0])
        self.assertIs(self.app.timetable_read_button, self.buttons["读取本人课表（既有接口）"][0])
        self.assertIs(self.app.live_catalog_button, self.buttons["读取 S202 目录"][0])
        self.assertIs(self.app.live_poll_button, self.buttons["自动刷新"][0])
        self.assertIs(self.app.start_button, self.buttons["开始只读监测"][0])
        self.assertIs(self.app.stop_button, self.buttons["停止"][0])
        self.assertIs(self.app.scheduled_start_button, self.buttons["提交未接入"][0])
        self.assertIs(self.app.scheduled_stop_button, self.buttons["取消"][0])

    def test_statuses_and_copy_shortcuts_keep_existing_variables_and_behavior(self):
        _, label, tree, _, _ = self._render()
        used_vars = [call.kwargs.get("textvariable") for call in label.call_args_list]
        for name in (
            "native_login_status", "native_preparation_status",
            "timetable_read_status", "live_catalog_status",
            "enrollment_gate_status",
        ):
            with self.subTest(name=name):
                self.assertIn(getattr(self.app, name), used_vars)

        bindings = tree.return_value.bind.call_args_list
        self.assertEqual([call.args[0] for call in bindings],
                         ["<Double-1>", "<Control-c>"])
        bindings[0].args[1](object())
        self.app.copy_selected_field.assert_called_once_with("course_id")

    def test_rendering_does_not_call_school_or_start_workers(self):
        _, labels, _, _, _ = self._render()
        for name in (
            "start_native_login", "check_native_preparation",
            "start_monitor", "check_live_catalog",
            "start_scheduled_enrollment", "read_config",
        ):
            getattr(self.app, name).assert_not_called()
        self.assertTrue(any(
            "自动提交尚未接入" in str(call)
            for call in labels.call_args_list))
        self.assertTrue(callable(Application._build))


if __name__ == "__main__":
    import unittest
    unittest.main()
