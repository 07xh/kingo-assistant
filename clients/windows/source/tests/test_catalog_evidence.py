"""Synthetic offline coverage for catalog evidence gates; no HAR or HTTP."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from catalog_evidence import assess_catalog_evidence, inspect_local_file


def sample():
    return {
        "version": 1, "source": "official_readonly_catalog",
        "official_selection_window_open": True,
        "official_ui_checked_by_user": True,
        "candidate_remaining_field": "remaining_candidate",
        "ui": {"observed_at": "2026-12-23T09:00:00+08:00",
               "rows": [{"key": "COURSE_A", "remaining": 0},
                        {"key": "COURSE_B", "remaining": 3}]},
        "response": {"observed_at": "2026-12-23T09:00:30+08:00",
                     "rows": [{"key": "COURSE_A", "remaining": 0},
                              {"key": "COURSE_B", "remaining": 3}]},
    }


class CatalogEvidenceTest(unittest.TestCase):
    def test_matching_still_requires_manual_review(self):
        result = assess_catalog_evidence(sample())
        self.assertEqual(result["status"], "ready_for_manual_review")
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["matched_rows"], 2)

    def test_closed_window_blocks(self):
        obj = sample()
        obj["official_selection_window_open"] = False
        self.assertEqual(assess_catalog_evidence(obj)["reason_codes"],
                         ["SELECTION_WINDOW_NOT_CONFIRMED"])

    def test_user_ui_confirmation_required(self):
        obj = sample()
        obj["official_ui_checked_by_user"] = False
        self.assertEqual(assess_catalog_evidence(obj)["reason_codes"],
                         ["OFFICIAL_UI_NOT_CONFIRMED"])

    def test_har_secret_fields_block(self):
        for field in ("token", "Cookie", "xqerSign", "encrptSecretKey",
                      "Authorization", "student_id"):
            with self.subTest(field=field):
                obj = sample()
                obj["response"][field] = "FAKE"
                self.assertEqual(assess_catalog_evidence(obj)["reason_codes"],
                                 ["UNSAFE_OR_INVALID_DOCUMENT"])

    def test_unconfirmed_course_fields_cannot_be_seats(self):
        for field in ("yixuan", "kex", "kcid", "skbj", "kcmc", "zxinfo", "zxxq"):
            obj = sample()
            obj["candidate_remaining_field"] = field
            self.assertEqual(assess_catalog_evidence(obj)["reason_codes"],
                             ["REMAINING_FIELD_NOT_ESTABLISHED"])

    def test_mismatch_fails_closed(self):
        obj = sample()
        obj["response"]["rows"][1]["remaining"] = 8
        result = assess_catalog_evidence(obj)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["matched_rows"], 1)

    def test_timestamp_guard(self):
        for value in ("2026-12-23T09:04:00+08:00", "2026-12-23T09:00:30", "bad"):
            obj = sample()
            obj["response"]["observed_at"] = value
            self.assertEqual(assess_catalog_evidence(obj)["reason_codes"],
                             ["TIMESTAMPS_TOO_FAR_APART_OR_INVALID"])

    def test_requires_zero_and_positive_samples(self):
        obj = sample()
        obj["ui"]["rows"][1]["remaining"] = 0
        obj["response"]["rows"][1]["remaining"] = 0
        self.assertEqual(assess_catalog_evidence(obj)["status"], "needs_more_evidence")

    def test_invalid_anonymous_rows(self):
        for bad in ({"key": "王同学", "remaining": 0},
                    {"key": "COURSE_A", "remaining": -1},
                    {"key": "COURSE_A", "remaining": True},
                    {"key": "COURSE_A", "remaining": 0, "course_name": "example"}):
            obj = sample()
            obj["ui"]["rows"][0] = bad
            self.assertIn(assess_catalog_evidence(obj)["reason_codes"][0],
                          ("ROW_MATCHING_UNDETERMINED", "UNSAFE_OR_INVALID_DOCUMENT"))

    def test_local_json_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            path.write_text(json.dumps(sample()), encoding="utf-8")
            self.assertEqual(inspect_local_file(path)["status"], "ready_for_manual_review")
            path.write_text("x" * (128 * 1024 + 1), encoding="utf-8")
            self.assertEqual(inspect_local_file(path)["reason_codes"], ["FILE_UNSAFE_OR_TOO_LARGE"])
            path.write_text("{bad", encoding="utf-8")
            self.assertEqual(inspect_local_file(path)["reason_codes"], ["INVALID_JSON"])


if __name__ == "__main__":
    unittest.main()
