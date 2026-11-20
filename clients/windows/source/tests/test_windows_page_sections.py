"""Independent Home and Tools pages keep the original Tk controller API.

No Tk display or network is required: fake widgets record command bindings and
layouts. The true Windows Tk smoke test verifies the resulting widget tree.
"""
from unittest import TestCase
from unittest.mock import Mock, patch

import windows_page_sections
from windows_gui import Application
from windows_page_sections import build_home_page, build_tools_page


class PageLayoutExtractionTests(TestCase):
    def setUp(self):
        self.app = Mock()
        self.app.tab_tools = object()

    def test_manual_builder_is_removed(self):
        self.assertFalse(hasattr(windows_page_sections, "build_manual_page"))
        self.assertTrue(callable(build_home_page))
        self.assertTrue(callable(build_tools_page))

    def test_tools_tab_keeps_all_live_fields_and_zero_automatic_calls(self):
        boxes = []
        def label_frame(*args, **kwargs):
            control = Mock()
            boxes.append((control, args, kwargs))
            return control

        with patch("windows_page_sections.ttk.Frame") as frame, \
             patch("windows_page_sections.ttk.Label") as label, \
             patch("windows_page_sections.ttk.LabelFrame",
                   side_effect=label_frame), \
             patch("windows_page_sections.ttk.Button") as button, \
             patch("windows_page_sections.ttk.Entry") as entry, \
             patch("windows_page_sections.tk.Text") as text:
            build_tools_page(self.app)
        self.assertEqual(len(boxes), 3)
        self.assertIs(self.app.tools_operations, boxes[0][0])
        self.assertIs(self.app.advanced_panel, boxes[1][0])
        self.assertIs(self.app.log_frame, boxes[2][0])
        self.app.advanced_panel.pack.assert_not_called()
        self.app.log_frame.pack.assert_called_once()
        text.assert_called_once_with(
            self.app.log_frame, height=7, state="disabled", wrap="word")
        self.assertIs(self.app.log_box, text.return_value)
        self.assertIs(self.app.config_entry, entry.return_value)
        self.assertEqual(entry.call_count, 2)
        entry.assert_any_call(
            self.app.advanced_panel, textvariable=self.app.path)
        entry.assert_any_call(
            frame.return_value,
            textvariable=self.app.lesson_detail_semester, width=16)

        calls = {
            call.kwargs["text"]: call.kwargs["command"]
            for call in button.call_args_list
        }
        expected = {
            "检查本机配置": "check_config",
            "核验应用内登录会话（只读）": "check_script_session",
            "核验学期接口（已抓包 · 只读）": "check_known_year_term",
            "核验已选课程结果网页（只读）": "check_known_school_results",
            "读取华水 S202 已选结果（只读）": "check_s202_selected_results",
            "读取本学期开课计划（只读快照）": "check_course_plan",
            "读取本人教务课表 AJAX（只读）": "check_lesson_detail",
            "核对当前 S202 轮次（只读）": "check_s202_round",
            "核对本人已选门数（只读）": "check_personal_s202_score",
            "核验初始化／资格预检 HAR（离线）": "inspect_ncwu_contracts",
            "复制脱敏契约报告": "copy_ncwu_contract_report",
            "核验提交 HAR（离线）": "inspect_ncwu_submit_har",
            "核对提交 HTML／JS（离线）": "inspect_ncwu_static_submit_files",
            "检查自动选课计划（离线）": "inspect_scheduled_runner",
            "展开 / 收起高级配置": "toggle_advanced_panel",
            "导出本机运行日志": "export_log",
        }
        for title, owner_method in expected.items():
            with self.subTest(title=title):
                self.assertIs(calls[title], getattr(self.app, owner_method))
                getattr(self.app, owner_method).assert_not_called()
        self.assertNotIn("自动提交", calls)
        self.assertEqual(len(calls), len(button.call_args_list))
        self.assertIs(self.app.s202_result_button, button.return_value)
        self.assertIs(self.app.course_plan_button, button.return_value)
        self.assertIs(self.app.lesson_detail_button, button.return_value)
        self.assertIs(self.app.term_probe_button, button.return_value)
        self.assertIs(self.app.scheduled_preflight_button, button.return_value)
        self.assertTrue(any(
            "不会证明学校接口已经接通" in str(call)
            for call in label.call_args_list
        ))

    def test_layout_module_is_stateless_and_application_still_owns_callbacks(self):
        self.assertNotIn("httpx", windows_page_sections.__dict__)
        for forbidden in ("KingoClient", "MonitorWorker", "session_cookie"):
            self.assertNotIn(forbidden, windows_page_sections.__dict__)
        self.assertTrue(callable(Application.toggle_advanced_panel))
        self.assertFalse(hasattr(Application, "toggle_portal_panel"))
        self.assertTrue(callable(Application.check_known_year_term))


if __name__ == "__main__":
    import unittest
    unittest.main()
