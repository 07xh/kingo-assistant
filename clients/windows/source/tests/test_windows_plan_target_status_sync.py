"""Show existing verified-scope course-plan identity matching in Home."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call

from core.ncwu_plan_alignment import PlanAlignmentReport, TargetAlignment
from windows_gui import Application


class PlanHomeSyncTests(TestCase):
    def test_same_course_and_internal_class_id_updates_home(self):
        table = Mock()
        table.exists.return_value = True
        owner = SimpleNamespace(
            table=table, _display_courses={
                "A": {"course_id": "C101", "class_id": "S202-CLASS-A"}},
        )
        report = PlanAlignmentReport("aligned", (
            TargetAlignment("A", "C101", "S202-CLASS-A", "matched_ids"),
        ))
        Application.apply_plan_alignment_rows(owner, report)
        table.set.assert_called_once_with(
            "A", "status", "开课计划：教学班匹配｜非实时余量")

    def test_changed_class_does_not_receive_stale_plan_result(self):
        table = Mock()
        table.exists.return_value = True
        owner = SimpleNamespace(
            table=table, _display_courses={
                "A": {"course_id": "C101", "class_id": "UPDATED-CLASS"}},
        )
        report = PlanAlignmentReport("aligned", (
            TargetAlignment("A", "C101", "OLD-CLASS", "matched_ids"),
        ))
        Application.apply_plan_alignment_rows(owner, report)
        table.set.assert_not_called()

    def test_semester_mismatch_keeps_non_successful_status(self):
        table = Mock()
        table.exists.return_value = True
        owner = SimpleNamespace(
            table=table, _display_courses={
                "A": {"course_id": "C101", "class_id": "CLASS-A"}},
        )
        report = PlanAlignmentReport("mismatch", (
            TargetAlignment("A", "C101", "CLASS-A", "semester_mismatch"),
        ))
        Application.apply_plan_alignment_rows(owner, report)
        table.set.assert_called_once_with(
            "A", "status", "开课计划：学期与计划不符")
