"""S202 course-code association is never class-ID or enrollment proof."""
from unittest import TestCase

from core.ncwu_live_elective import LiveElectiveCourse, LiveElectiveSnapshot
from core.ncwu_live_target_review import compare_live_targets


def course(code="000101", class_no="VISIBLE_01", teacher="李老师",
           name="工程制图"):
    return LiveElectiveCourse(
        course_name=name, course_code=code,
        class_number=class_no, teacher=teacher,
        selection_status="需核对", points_label="20",
    )


def snapshot(*rows, year="2026", term="0"):
    return LiveElectiveSnapshot(
        "catalog_read", "catalog", "reported_open",
        rows, 17, academic_year=year, academic_term=term,
    )


def target(code="000101", class_id="INTERNAL_SKBJDM",
           teacher="李老师", name="工程制图", key="A"):
    return {
        "key": key, "course_id": code, "class_id": class_id,
        "course_name": name, "teacher": teacher,
    }


def config(*targets, sem="2026-2027-1"):
    return {"courses": list(targets), "xnxq": sem}


class LiveS202TargetReviewTests(TestCase):
    def test_same_term_one_code_match_is_only_a_course_candidate(self):
        result = compare_live_targets(snapshot(course()), config(target()))
        self.assertEqual(result.semester_state, "aligned")
        self.assertEqual(result.candidate_targets, 1)
        self.assertEqual(result.targets[0].status, "candidate")
        self.assertEqual(result.row_marks, (("A",),))
        self.assertIn("不能据此验证本地 class_id/skbjdm", result.message)
        self.assertIn("不能", result.message)

    def test_visible_class_number_never_validates_internal_class_id(self):
        result = compare_live_targets(
            snapshot(course(class_no="SAME_STRING")),
            config(target(class_id="SAME_STRING")),
        )
        self.assertEqual(result.candidate_targets, 1)
        self.assertNotIn("教学班确认", result.message.splitlines()[0])
        self.assertEqual(result.targets[0].configured_class_id, "SAME_STRING")
        self.assertIn("教学班真实标识尚未核对", result.targets[0].label)

    def test_mismatched_semester_never_marks_rows(self):
        result = compare_live_targets(
            snapshot(course(), term="1"),
            config(target(), sem="2026-2027-1"),
        )
        self.assertEqual(result.targets[0].status, "semester_mismatch")
        self.assertEqual(result.row_marks, ((),))
        self.assertEqual(result.candidate_targets, 0)

    def test_unknown_server_semester_is_not_a_verified_match(self):
        result = compare_live_targets(
            snapshot(course(), year="", term=""),
            config(target()),
        )
        self.assertEqual(result.semester_state, "unverified")
        self.assertEqual(result.targets[0].status, "semester_unverified")
        self.assertEqual(result.row_marks, ((),))

    def test_course_not_seen_does_not_mean_absent_from_unpaged_catalog(self):
        result = compare_live_targets(snapshot(course()), config(target(code="OTHER")))
        self.assertEqual(result.targets[0].status, "not_seen")
        self.assertIn("不能推定整个学期没有该课", result.message)

    def test_teacher_or_course_name_conflict_does_not_mark_row(self):
        for want in (
            target(teacher="另一个老师"),
            target(name="别的课程"),
        ):
            result = compare_live_targets(snapshot(course()), config(want))
            self.assertEqual(result.targets[0].status, "metadata_conflict")
            self.assertEqual(result.row_marks[0], ())

    def test_unfilled_school_metadata_is_uncertain(self):
        result = compare_live_targets(
            snapshot(course(teacher="")),
            config(target()),
        )
        self.assertEqual(result.targets[0].status, "metadata_unknown")
        self.assertEqual(result.row_marks[0], ())

    def test_multiple_visible_classes_remain_candidates_not_exact_choice(self):
        result = compare_live_targets(
            snapshot(course(class_no="A01"), course(class_no="B02")),
            config(target()),
        )
        self.assertEqual(result.targets[0].visible_courses, 2)
        self.assertEqual(result.targets[0].matching_rows, (0, 1))
        self.assertEqual(result.row_marks, (("A",), ("A",)))
        self.assertIn("（可见同代码 2 条）", result.message)

    def test_targets_are_never_imported_or_automatically_changed(self):
        cfg = config(target(code="000101", class_id="INTERNAL_01"))
        result = compare_live_targets(snapshot(course()), cfg)
        self.assertEqual(cfg["courses"][0]["class_id"], "INTERNAL_01")
        self.assertEqual(result.targets[0].configured_class_id, "INTERNAL_01")

    def test_missing_template_and_bad_config_fail_closed(self):
        missing = compare_live_targets(
            snapshot(course()),
            config(target(code="FILL_FROM_OFFICIAL_PAGE")),
        )
        self.assertEqual(missing.targets[0].status, "missing_target")
        self.assertEqual(missing.row_marks, ((),))
        with self.assertRaises(ValueError):
            compare_live_targets(snapshot(course()), {"courses": {"key": "X"}})
        with self.assertRaises(ValueError):
            compare_live_targets(LiveElectiveSnapshot("form_missing"), config(target()))

    def test_empty_targets_does_not_make_up_preferences(self):
        result = compare_live_targets(snapshot(course()), config())
        self.assertEqual(result.targets, ())
        self.assertEqual(result.row_marks, ((),))
        self.assertIn("本机没有目标课程", result.message)


if __name__ == "__main__":
    import unittest
    unittest.main()
