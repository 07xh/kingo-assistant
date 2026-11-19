"""Manual and diagnostics pages keep the original Tk controller API.

No Tk display or network is required: fake widgets record command bindings
and layouts. The true Windows Tk smoke test verifies the widget tree.
"""
from unittest import TestCase
from unittest.mock import Mock, patch

import windows_page_sections
from windows_gui import Application
from windows_page_sections import build_manual_page, build_tools_page


class PageLayoutExtractionTests(TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.tab_manual = object()
        self.app.tab_tools = object()
        self.groups = []
        self.buttons = {}
        self.labels = []

    def _group(self, parent, **kwargs):
        control = Mock(name=kwargs["text"])
        self.groups.append((control, parent, kwargs))
        return control

    def _button(self, parent, **kwargs):
        control = Mock(name=kwargs["text"])
        self.buttons[kwargs["text"]] = (control, parent, kwargs)
        return control

    def _label(self, parent, **kwargs):
        control = Mock(name="label")
        self.labels.append((control, parent, kwargs))
        return control

    def test_manual_page_is_only_navigation_and_owner_callbacks(self):
        with patch("windows_page_sections.ttk.Label", side_effect=self._label), \
             patch("windows_page_sections.ttk.Frame"), \
             patch("windows_page_sections.ttk.LabelFrame",
                   side_effect=self._group), \
             patch("windows_page_sections.ttk.Button",
                   side_effect=self._button), \
             patch("windows_page_sections.ttk.Treeview") as entries, \
             patch("windows_page_sections.ttk.Scrollbar"), \
             patch("windows_page_sections.tk.Frame"):
            build_manual_page(self.app)
        self.assertEqual(
            [group[2]["text"].strip() for group in self.groups],
            [
                "手动操作（只调用系统浏览器）",
                "华水官方入口清单（静态内置 · 无联网）",
                "官网入口与到点提醒（不会自动提交）",
            ])
        self.assertIs(self.app.portal_panel, self.groups[2][0])
        # The entry inventory is filled from the shipped static registry, so
        # the page has no empty shell and still issues no request.
        self.assertIs(self.app.portal_entries_table, entries.return_value)
        inserted = entries.return_value.insert.call_args_list
        self.assertGreaterEqual(len(inserted), 3)
        self.assertEqual(set(entries.return_value.insert.call_args.kwargs),
                         {"iid", "values"})
        self.assertEqual(set(self.buttons), {
            "设置到点打开官网",
            "现在打开华水官网",
            "复制所选课程代码并打开官网",
            "检查软件登录状态（只读）",
        })
        self.assertIs(self.buttons["设置到点打开官网"][2]["command"],
                      self.app.toggle_portal_appointment)
        self.assertIs(self.buttons["现在打开华水官网"][2]["command"],
                      self.app.open_official_portal)
        self.assertIs(self.buttons["复制所选课程代码并打开官网"][2]["command"],
                      self.app.prepare_selected_course)
        self.assertIs(self.buttons["检查软件登录状态（只读）"][2]["command"],
                      self.app.check_script_session)
        self.assertIs(self.app.session_check_button,
                      self.buttons["检查软件登录状态（只读）"][0])
        self.app.open_official_portal.assert_not_called()
        self.app.check_script_session.assert_not_called()
        self.assertTrue(any(
            "登录、选课与提交均由本人完成" in str(call)
            for call in self.labels))

    def test_tools_tab_keeps_all_live_fields_and_zero_automatic_calls(self):
        log_box = Mock(name="log")

        with patch("windows_page_sections.ttk.Label",
                   side_effect=self._label), \
             patch("windows_page_sections.ttk.Frame"), \
             patch("windows_page_sections.ttk.LabelFrame",
                   side_effect=self._group), \
             patch("windows_page_sections.ttk.Button",
                   side_effect=self._button), \
             patch("windows_page_sections.ttk.Entry") as entry, \
             patch("windows_page_sections.ttk.Scrollbar"), \
             patch("windows_page_sections.tk.Text",
                   return_value=log_box) as text, \
             patch("windows_page_sections.tk.Frame"):
            build_tools_page(self.app)

        self.assertEqual(
            [group[2]["text"].strip() for group in self.groups],
            [
                "本机诊断（不发起选课提交）",
                "学校接口只读核验",
                "高级协议配置 · 仅适合调试",
                "运行日志（不显示账号或凭证）",
            ])
        self.assertIs(self.app.tools_operations, self.groups[0][0])
        self.assertIs(self.app.advanced_panel, self.groups[2][0])
        self.assertIs(self.app.log_frame, self.groups[3][0])
        self.app.advanced_panel.pack.assert_not_called()
        self.app.log_frame.pack.assert_called_once()

        text.assert_called_once()
        self.assertIs(self.app.log_box, log_box)
        text_kwargs = text.call_args.kwargs
        self.assertEqual(text_kwargs["state"], "disabled")
        self.assertEqual(text_kwargs["height"], 6)
        self.assertEqual(text_kwargs["font"][0], "Consolas")

        self.assertIs(self.app.config_entry, entry.return_value)
        entry_calls = entry.call_args_list
        semester = [call for call in entry_calls
                    if call.kwargs.get("textvariable")
                    is self.app.lesson_detail_semester]
        self.assertEqual(len(semester), 1)
        self.assertEqual(semester[0].kwargs["width"], 14)
        config = [call for call in entry_calls
                  if call.kwargs.get("textvariable") is self.app.path]
        self.assertEqual(len(config), 1)
        self.assertIs(config[0].args[0], self.app.advanced_panel)

        expected = {
            "检查本机配置": "check_config",
            "登录 HAR 分析（离线）": "audit_auth_route_har",
            "课程 HAR 分析（离线）": "audit_local_har",
            "选课表头 HTML 核验（离线）": "inspect_elective_table_file",
            "课程目录证据对照": "review_catalog_evidence",
            "离线 HTML 演练": "replay_local",
            "虚构示例（仅演示）": "demo_next",
            "本机离线调度测速": "benchmark_offline",
            "查看学校官方入口": "show_official_entries",
            "检测官方登录页可达性": "probe_official_entries",
            "展开 / 收起高级配置": "toggle_advanced_panel",
            "检查自动选课计划（离线）": "inspect_scheduled_runner",
            "核验已选课程结果网页（只读）": "check_known_school_results",
            "读取华水 S202 已选结果（只读）": "check_s202_selected_results",
            "读取本学期开课计划（只读快照）": "check_course_plan",
            "核对当前 S202 轮次（只读）": "check_s202_round",
            "核对本人已选门数（只读）": "check_personal_s202_score",
            "核验学期接口（已抓包 · 只读）": "check_known_year_term",
            "读取本人教务课表 AJAX（只读）": "check_lesson_detail",
            "核验初始化／资格预检 HAR（离线）": "inspect_ncwu_contracts",
            "复制脱敏契约报告": "copy_ncwu_contract_report",
            "导出本机运行日志": "export_log",
            "选择…": "pick_config",
            "打开文件": "open_config",
        }
        self.assertEqual(set(self.buttons), set(expected))
        for title, owner_method in expected.items():
            with self.subTest(title=title):
                self.assertIs(self.buttons[title][2]["command"],
                              getattr(self.app, owner_method))
                getattr(self.app, owner_method).assert_not_called()
        self.assertNotIn("自动提交", self.buttons)
        self.assertIs(self.app.s202_result_button,
                      self.buttons["读取华水 S202 已选结果（只读）"][0])
        self.assertIs(self.app.course_plan_button,
                      self.buttons["读取本学期开课计划（只读快照）"][0])
        self.assertIs(self.app.lesson_detail_button,
                      self.buttons["读取本人教务课表 AJAX（只读）"][0])
        self.assertIs(self.app.term_probe_button,
                      self.buttons["核验学期接口（已抓包 · 只读）"][0])
        self.assertIs(self.app.scheduled_preflight_button,
                      self.buttons["检查自动选课计划（离线）"][0])
        # The verification block is a two-column grid: aligned buttons left,
        # the live status of each read on the right.
        verify_group = self.groups[1][0]
        for title in (
            "核验已选课程结果网页（只读）",
            "读取华水 S202 已选结果（只读）",
            "读取本学期开课计划（只读快照）",
            "核对当前 S202 轮次（只读）",
            "核对本人已选门数（只读）",
            "核验学期接口（已抓包 · 只读）",
        ):
            with self.subTest(gridded=title):
                control, parent, _ = self.buttons[title]
                self.assertIs(parent, verify_group)
                control.grid.assert_called()
        self.assertTrue(any(
            "不会证明学校写接口已接通" in str(call)
            for call in self.labels))

    def test_layout_module_is_stateless_and_application_still_owns_callbacks(self):
        self.assertNotIn("httpx", windows_page_sections.__dict__)
        for forbidden in ("KingoClient", "MonitorWorker", "session_cookie"):
            self.assertNotIn(forbidden, windows_page_sections.__dict__)
        self.assertTrue(callable(Application.toggle_advanced_panel))
        self.assertTrue(callable(Application.toggle_portal_panel))
        self.assertTrue(callable(Application.check_known_year_term))


if __name__ == "__main__":
    import unittest
    unittest.main()
