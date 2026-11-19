"""Synthetic S202 HTML result-candidate comparison; no school network."""
from unittest import TestCase

from core.ncwu_s202_candidate_review import compare_s202_candidate_targets
from core.ncwu_s202_readback import CandidateSelectedCourse, S202ReadbackResult


def data(*rows):
    return S202ReadbackResult("page_observed", 1, 1, tuple(rows))


def plan(**course):
    return {"courses": [dict(key="A", course_id="C101",
                             class_id="INTERNAL_SKBJDM", **course)]}


class CandidateReviewTests(TestCase):
    def test_matching_display_class_never_becomes_internal_confirmed_class(self):
        result = data(CandidateSelectedCourse(
            "C101", "VISIBLE-SKBH", "工程制图", "教师A"))
        report = compare_s202_candidate_targets(
            result, plan(course_name="工程制图", teacher="教师A"))
        self.assertEqual(report.candidate_count, 1)
        self.assertEqual(report.targets[0].matching_rows, (0,))
        self.assertEqual(report.row_marks, (("课程候选：A",),))
        self.assertIn("内部 skbjdm", report.message)
        self.assertIn("均未确认已选", report.message)
        self.assertNotIn("INTERNAL_SKBJDM", repr(report.row_marks))

    def test_visible_class_matching_config_still_not_verified(self):
        report = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("C101", "VISIBLE")),
            {"courses": [{"key": "A", "course_id": "C101", "class_id": "VISIBLE"}]})
        self.assertEqual(report.targets[0].state, "candidate")
        self.assertIn("不能转换成正式已选记录", report.message)

    def test_two_same_course_rows_are_ambiguous(self):
        report = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("C101", "1"),
                 CandidateSelectedCourse("C101", "2")), plan())
        self.assertEqual(report.candidate_count, 0)
        self.assertEqual(report.ambiguous_count, 1)
        self.assertEqual(report.row_marks,
                         (("课程候选：A",), ("课程候选：A",)))

    def test_absent_or_wrong_metadata_is_not_success(self):
        report = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("OTHER", "1"),
                 CandidateSelectedCourse("C101", "2", "高数", "教师B")),
            plan(course_name="工程制图", teacher="教师A"))
        self.assertEqual(report.targets[0].state, "metadata_conflict")
        self.assertEqual(report.row_marks, ((), ()))
        no_code = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("OTHER", "1")), plan())
        self.assertEqual(no_code.targets[0].state, "not_seen")

    def test_missing_metadata_is_unknown_not_matching(self):
        report = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("C101", "1", "", "")),
            plan(course_name="工程制图", teacher="教师A"))
        self.assertEqual(report.targets[0].state, "metadata_unknown")
        self.assertEqual(report.candidate_count, 0)

    def test_invalid_local_plan_and_failed_read_are_rejected(self):
        for config in ({"courses": "bad"},
                       {"courses": [None]},
                       {"courses": [{} for _ in range(31)]},
                       None):
            with self.subTest(config=config), self.assertRaises(ValueError):
                compare_s202_candidate_targets(data(), config)
        with self.assertRaises(ValueError):
            compare_s202_candidate_targets(S202ReadbackResult("unavailable"),
                                           plan())

    def test_no_local_preference_does_not_discard_candidates(self):
        report = compare_s202_candidate_targets(
            data(CandidateSelectedCourse("C101", "1")), {"courses": []})
        self.assertEqual(report.targets, ())
        self.assertEqual(report.row_marks, ((),))
