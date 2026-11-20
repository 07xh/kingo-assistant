"""Verify workbench controls use the existing Application callbacks."""
from contextlib import ExitStack
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_page_sections import build_home_page


class HomePageViewTests(TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.enrollment_gate_label.return_value = "自动提交尚未接入"
        self.buttons = {}
        self.groups = []
        self.tables = []

        def make_button(parent, **kwargs):
            button = Mock()
            self.buttons[kwargs["text"]] = (button, kwargs)
            return button

        def make_group(parent, **kwargs):
            group = Mock()
            self.groups.append((group, parent, kwargs["text"]))
            return group

        def make_table(parent, columns, titles, widths, **kwargs):
            tree = Mock()
            self.tables.append((tree, parent, columns))
            return tree

        with ExitStack() as stack:
            for path, options in (
                ("ttk.Frame", {}), ("ttk.Label", {}), ("ttk.Entry", {}),
                ("ttk.Scrollbar", {}), ("tk.Text", {}), ("tk.StringVar", {}),
                ("ttk.Button", {"side_effect": make_button}),
                ("ttk.LabelFrame", {"side_effect": make_group}),
                ("_table_panel", {"side_effect": make_table}),
                ("display_scale", {"return_value": 1}),
            ):
                stack.enter_context(patch(f"windows_page_sections.{path}", **options))
            build_home_page(self.app)

    def test_target_list_log_and_plan_share_one_row(self):
        targets, log, plan = self.groups
        self.assertEqual([group[2] for group in self.groups], ["目标任务", "运行日志", "执行计划"])
        self.assertIs(targets[1], log[1])
        self.assertIs(log[1], plan[1])
        self.assertIs(self.app._live_catalog_tree, self.tables[0][0])
        self.assertIs(self.app.table, self.tables[1][0])
        self.assertIs(self.app.log_frame, log[0])
        bind = self.app.table.bind.call_args
        self.assertEqual(bind.args[0], "<Double-1>")
        bind.args[1](object())
        self.app.copy_selected_field.assert_called_once_with("course_id")

    def test_commands_wire_existing_callbacks_without_running_them(self):
        bindings = {
            "编辑目标": "edit_settings",
            "课程方案": "manage_plans",
            "查询课程": "check_live_catalog",
            "自动刷新": "toggle_live_polling",
            "加入目标": "add_catalog_course",
            "候选分析": "show_live_target_review",
            "开始余量监测": "start_monitor",
            "停止": "stop_monitor",
            "启动前检查": "preflight",
            "提交条件": "show_enrollment_gate_details",
            "自动提交": "start_scheduled_enrollment",
            "取消自动计划": "stop_scheduled_enrollment",
        }
        for text, name in bindings.items():
            with self.subTest(text=text):
                _, kwargs = self.buttons[text]
                self.assertIs(kwargs["command"], getattr(self.app, name))
                getattr(self.app, name).assert_not_called()
        for name in ("自动提交", "取消自动计划", "停止", "加入目标"):
            self.assertEqual(self.buttons[name][1]["state"], "disabled")
