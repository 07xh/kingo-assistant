"""Headless GUI regressions for S202 candidate-to-target review."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.ncwu_s202_readback import CandidateSelectedCourse, S202ReadbackResult
from windows_gui import Application


class CandidateReviewGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            path=SimpleNamespace(get=lambda: "config.yaml"),
        )
        self.result = S202ReadbackResult(
            "page_observed", 1, 1,
            (CandidateSelectedCourse("C101", "VISIBLE-CLASS", "工程制图", "教师A"),),
        )
        self.config = {"courses": [{
            "key": "A", "course_id": "C101",
            "class_id": "INTERNAL_SKBJDM",
            "course_name": "工程制图", "teacher": "教师A",
        }]}

    def test_popup_links_only_course_candidate_not_internal_class(self):
        with patch("windows_gui.read_config", return_value=self.config), \
             patch("windows_gui._open_readonly_popup") as popup, \
             patch("windows_gui._create_readonly_tree") as tree, \
             patch("windows_gui.ttk.Frame"), \
             patch("windows_gui.ttk.Label"), \
             patch("windows_gui.ttk.Button") as button:
            Application.show_s202_candidate_results(self.app, self.result)
        self.assertEqual(self.app._s202_candidate_report.candidate_count, 1)
        self.assertIn("candidate", tree.call_args.kwargs["columns"])
        args = tree.return_value.insert.call_args.kwargs["values"]
        self.assertIn("VISIBLE-CLASS", args)
        self.assertIn("课程候选：A", args)
        self.assertNotIn("INTERNAL_SKBJDM", str(args))
        self.assertNotIn("成功", str(args))
        button.assert_called_once()
        popup.assert_called_once()

    def test_invalid_config_still_displays_original_candidates(self):
        with patch("windows_gui.read_config", side_effect=ValueError), \
             patch("windows_gui._open_readonly_popup"), \
             patch("windows_gui._create_readonly_tree") as tree, \
             patch("windows_gui.ttk.Frame"), \
             patch("windows_gui.ttk.Label"), \
             patch("windows_gui.ttk.Button") as button:
            Application.show_s202_candidate_results(self.app, self.result)
        self.assertIsNone(self.app._s202_candidate_report)
        tree.return_value.insert.assert_called_once()
        self.assertIn("未建立候选关联",
                      tree.return_value.insert.call_args.kwargs["values"])
        button.assert_not_called()
