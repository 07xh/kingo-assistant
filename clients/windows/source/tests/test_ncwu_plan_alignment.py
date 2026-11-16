"""Offline preference-vs-school-plan checks, never a selection verdict."""
from unittest import TestCase

from core.ncwu_course_plan import CoursePlanRow, PlanSnapshot
from core.ncwu_plan_alignment import compare_plan_targets


def row(code="001234", class_id="C01", *, name="工程制图", teacher="张老师"):
    return CoursePlanRow(
        code=code, name=name, class_id=class_id,
        class_name="", teacher=teacher, time="周一",
        room="", capacity=50, enrolled=40, plan_available=10)


def snap(*rows, year="2026", term="0"):
    return PlanSnapshot(
        "read", tuple(rows), len(rows), "table",
        academic_year=year, academic_term=term)


def cfg(*targets, semester="2026-2027-1"):
    return {"xnxq": semester, "courses": list(targets)}


def choice(code="001234", class_id="C01", *,
           key="A", name="工程制图", teacher="张老师"):
    return {
        "key": key, "course_id": code, "class_id": class_id,
        "course_name": name, "teacher": teacher,
    }


class PlanTargetMatchingTests(TestCase):
    def test_exact_matching_is_only_identifier_alignment(self):
        result = compare_plan_targets(snap(row()), cfg(choice()))
        self.assertEqual(result.semester_status, "aligned")
        self.assertEqual(result.confirmed_ids, 1)
        self.assertEqual(result.targets[0].status, "matched_ids")
        self.assertIn("不等于", result.message)
        self.assertIn("选课资格", result.message)

    def test_second_semester_uses_index_one(self):
        report = compare_plan_targets(
            snap(row(), term="1"),
            cfg(choice(), semester="2026-2027-2"))
        self.assertEqual(report.targets[0].status, "matched_ids")
        self.assertEqual(report.semester_status, "aligned")

    def test_different_semester_blocks_all_course_matching(self):
        report = compare_plan_targets(
            snap(row(), term="0"),
            cfg(choice(), semester="2026-2027-2"))
        self.assertEqual(report.semester_status, "mismatch")
        self.assertEqual(report.targets[0].status, "semester_mismatch")
        self.assertEqual(report.confirmed_ids, 0)

    def test_unknown_semester_is_not_assumed(self):
        for sem in ("", "2026/1", "2026-2026-1", "2026-2027-3"):
            with self.subTest(sem=sem):
                report = compare_plan_targets(
                    snap(row()), cfg(choice(), semester=sem))
                self.assertEqual(report.semester_status, "unverified")
                self.assertEqual(report.confirmed_ids, 0)
        self.assertEqual(
            compare_plan_targets(snap(row(), year=""), cfg(choice())).targets[0].status,
            "semester_unverified")

    def test_missing_course_and_missing_class_are_not_confirmed(self):
        report = compare_plan_targets(
            snap(row()),
            cfg(choice(code="FILL_FROM_OFFICIAL_PAGE"),
                choice(code="WRONG", key="B"),
                choice(class_id="", key="C")))
        self.assertEqual(
            [x.status for x in report.targets],
            ["course_id_missing", "course_not_in_plan", "class_id_missing"])
        self.assertEqual(report.targets[2].candidates, 1)

    def test_class_id_must_be_exact_no_conversion_from_visible_number(self):
        report = compare_plan_targets(
            snap(row(class_id="ORIGINAL_SKBJDM")),
            cfg(choice(class_id="VISIBLE_SKBH")))
        self.assertEqual(report.targets[0].status, "class_unmatched")
        self.assertEqual(report.confirmed_ids, 0)

    def test_missing_school_class_identity_is_unknown_not_mismatch(self):
        report = compare_plan_targets(snap(row(class_id="")),
                                      cfg(choice(class_id="C01")))
        self.assertEqual(report.targets[0].status, "class_unknown")

    def test_duplicate_same_class_is_ambiguous(self):
        report = compare_plan_targets(
            snap(row(), row(teacher="另一教师")), cfg(choice()))
        self.assertEqual(report.targets[0].status, "ambiguous")

    def test_name_and_teacher_mismatch_is_reported(self):
        report = compare_plan_targets(
            snap(row(teacher="李老师")), cfg(choice()))
        self.assertEqual(report.targets[0].status, "metadata_conflict")
        self.assertEqual(report.confirmed_ids, 0)

    def test_missing_teacher_is_unverified_not_conflict(self):
        report = compare_plan_targets(snap(row(teacher="")), cfg(choice()))
        self.assertEqual(report.targets[0].status, "metadata_unverified")

    def test_no_targets_and_invalid_config_fail_safe(self):
        report = compare_plan_targets(snap(row()), cfg())
        self.assertEqual(report.targets, ())
        self.assertIn("未配置目标志愿", report.message)
        with self.assertRaises(ValueError):
            compare_plan_targets(snap(row()), {"courses": {}})
        with self.assertRaises(ValueError):
            compare_plan_targets(PlanSnapshot("form_unavailable"), cfg(choice()))

    def test_plan_seats_not_reported_as_selection_availability(self):
        report = compare_plan_targets(snap(row()), cfg(choice()))
        self.assertNotIn("可抢", report.message)
        self.assertNotIn("10", report.message)
        self.assertIn("开课计划仅为只读快照", report.message)


if __name__ == "__main__":
    import unittest
    unittest.main()
