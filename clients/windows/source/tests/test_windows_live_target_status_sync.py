"""S202 course candidates update the existing Home table without another request."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, call

from core.ncwu_live_target_review import LiveTargetItem, LiveTargetReport
from windows_gui import Application


def report(*targets):
    return LiveTargetReport(
        semester_state="aligned", targets=tuple(targets), row_marks=()
    )


class LiveTargetHomeSyncTests(TestCase):
    def test_fresh_course_candidates_are_visible_on_home_without_inferred_seats(self):
        table = Mock()
        table.exists.side_effect = lambda key: key in ("first", "second")
        app = SimpleNamespace(
            table=table,
            _display_courses={
                "first": {"course_id": "C101", "class_id": "INTERNAL-1"},
                "second": {"course_id": "C102", "class_id": "INTERNAL-2"},
            },
        )
        Application.apply_live_target_rows(app, report(
            LiveTargetItem("first", "C101", "INTERNAL-1", "candidate"),
            LiveTargetItem("second", "C102", "INTERNAL-2", "not_seen"),
        ))
        self.assertEqual(table.set.call_args_list, [
            call("first", "status", "官网课程候选｜教学班未核验"),
            call("second", "status", "本次页面未见｜可能分页"),
        ])

    def test_changed_course_plan_or_nonexistent_rows_remain_untouched(self):
        table = Mock()
        table.exists.return_value = False
        app = SimpleNamespace(
            table=table, _display_courses={
                "first": {"course_id": "C101", "class_id": "NEW-CLASS"},
                "second": {"course_id": "C102", "class_id": "INTERNAL-2"},
            },
        )
        Application.apply_live_target_rows(app, report(
            LiveTargetItem("first", "C101", "OLD-CLASS", "candidate"),
            LiveTargetItem("second", "C102", "INTERNAL-2", "candidate"),
        ))
        table.set.assert_not_called()

    def test_unverified_semester_never_becomes_success_or_confirmed_availability(self):
        table = Mock()
        table.exists.return_value = True
        app = SimpleNamespace(
            table=table,
            _display_courses={"first": {
                "course_id": "C101", "class_id": "INTERNAL-1"}},
        )
        Application.apply_live_target_rows(app, report(
            LiveTargetItem("first", "C101", "INTERNAL-1", "semester_unverified"),
        ))
        self.assertEqual(table.set.call_args.args, (
            "first", "status", "服务器学期未核验"))
