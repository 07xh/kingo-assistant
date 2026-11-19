"""Offline exact-target comparison; candidates must never be success proof."""
from unittest import TestCase

from core.ncwu_known_result_candidates import HistoricalClassCandidate
from core.ncwu_selected_target_review import compare_result_targets


def plan(*items):
    return {"courses": [dict(key=f"T{i}", course_id=code, class_id=klass)
                        for i, (code, klass) in enumerate(items, 1)]}


def review(candidates, config, *, scoped=True):
    return compare_result_targets(
        tuple(candidates), config, account_bound=scoped, semester_bound=scoped)


class CandidateTargetReviewTests(TestCase):
    def test_exact_class_and_historical_state_pair_remain_unconfirmed(self):
        rows = (HistoricalClassCandidate("COURSE1", "CLASS1", "选中", "03"),)
        output = review(rows, plan(("COURSE1", "CLASS1")))
        self.assertEqual(output.targets[0].state, "observed_pair")
        self.assertEqual(output.matched_candidates, 1)
        self.assertEqual(output.row_marks, (("志愿 T1：精确候选",),))
        self.assertIn("不能推定当前选课轮次", output.message)
        self.assertIn("仍未确认选课", output.message)
        self.assertNotIn("COURSE1", output.message)
        self.assertNotIn("CLASS1", output.message)

    def test_unscoped_snapshot_does_not_mark_matching_result(self):
        rows = (HistoricalClassCandidate("COURSE1", "CLASS1", "选中", "03"),)
        for account, semester in ((True, False), (False, True), (False, False)):
            result = compare_result_targets(
                rows, plan(("COURSE1", "CLASS1")),
                account_bound=account, semester_bound=semester)
            self.assertEqual(result.targets[0].state, "scope_unverified")
            self.assertFalse(result.session_scope_checked)
            self.assertEqual(result.matched_candidates, 0)

    def test_same_course_different_class_is_explicit(self):
        rows = (HistoricalClassCandidate("COURSE1", "WRONG", "选中", "03"),)
        output = review(rows, plan(("COURSE1", "CLASS1")))
        self.assertEqual(output.targets[0].state, "different_class")
        self.assertEqual(output.row_marks, ((),))

    def test_multiple_classes_are_ambiguous_even_with_exact_match(self):
        rows = (
            HistoricalClassCandidate("COURSE1", "CLASS1", "选中", "03"),
            HistoricalClassCandidate("COURSE1", "CLASS2", "选中", "03"),
        )
        output = review(rows, plan(("COURSE1", "CLASS1")))
        self.assertEqual(output.targets[0].state, "multiple_classes")
        self.assertEqual(output.matched_candidates, 0)

    def test_unsupported_status_code_does_not_become_success(self):
        for shown, code in (("选中", ""), ("选中", "09"), ("未选", "03"),
                            ("", "03"), ("未知", "XX")):
            with self.subTest(shown=shown, code=code):
                output = review(
                    (HistoricalClassCandidate(
                        "COURSE1", "CLASS1", shown, code),),
                    plan(("COURSE1", "CLASS1")))
                self.assertEqual(output.targets[0].state, "status_unverified")
                self.assertEqual(output.matched_candidates, 0)

    def test_not_seen_or_invalid_local_target(self):
        out = review(
            (HistoricalClassCandidate("OTHER", "CLASS1"),),
            plan(("COURSE1", "CLASS1"), ("COURSE1", "FILL_FROM_CATALOG")))
        self.assertEqual([v.state for v in out.targets],
                         ["not_seen", "invalid_target"])

    def test_empty_and_bad_config(self):
        out = review((), {"courses": []})
        self.assertEqual(out.targets, ())
        self.assertEqual(out.matched_candidates, 0)
        for bad in (None, {"courses": "bad"}, {"courses": [None]},
                    {"courses": [{} for _ in range(31)]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                review((), bad)

    def test_invalid_result_record_type_rejected(self):
        with self.assertRaises(ValueError):
            review((("COURSE", "CLASS"),), plan(("COURSE", "CLASS")))
