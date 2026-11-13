"""Regression coverage for the safe portion of kingo-live HAR research port."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.har_schema import inspect_offline_readonly_shape
from core.har_audit import summarize_har, format_summary


def response(rows=None, *, content_type="application/json", extra=None, status=200):
    value = {"data": {"rows": rows or []}, "session_token": "FAKE_SECRET"}
    if extra:
        value.update(extra)
    return {"status": status, "content": {"mimeType": content_type,
                                             "text": json.dumps(value, ensure_ascii=False)}}


def entry(path, result, method="GET"):
    return {"request": {"url": "https://jwmis.ncwu.edu.cn" + path,
                        "method": method, "headers": [{"name": "Cookie", "value": "FAKE_SECRET"}]},
            "response": result}


class SafeShapeIntegrationTests(unittest.TestCase):
    def test_candidate_is_only_a_review_signal(self):
        body = response([{"kcid": "PRIVATE-COURSE", "skbj": "PRIVATE-CLASS",
                          "kyl": 2, "teacher": "PRIVATE_NAME"}])
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body),
                         "candidate_requires_manual_review")

    def test_timetable_always_excluded(self):
        body = response([{"kcid": "X", "skbj": "Y", "kyl": 1}])
        self.assertEqual(inspect_offline_readonly_shape("/hsjw/wap/mycourseschedule.action", "GET", body),
                         "timetable_excluded")
        body = response([{"kcid": "X", "skbj": "Y", "kyl": 1}], extra={"week1": []})
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "timetable_excluded")

    def test_closed_window_never_becomes_catalog(self):
        body = response([{"kcid": "X", "skbj": "Y", "kyl": 1}], extra={"prohibit": "1"})
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "window_closed_excluded")
        body = response([], extra={"message": "当前时间不是有效的选课时间区段!"})
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "window_closed_excluded")

    def test_write_calls_cannot_verify_candidate(self):
        body = response([{"kcid": "X", "skbj": "Y", "kyl": 1}])
        self.assertEqual(inspect_offline_readonly_shape("/list", "POST", body), "not_applicable")

    def test_capacity_is_not_remaining(self):
        body = response([{"kcid": "X", "skbj": "Y", "rl": 30, "yixuan": 9}])
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "fields_incomplete")

    def test_invalid_or_encoded_response_ignored(self):
        body = response([{"kcid": "X", "skbj": "Y", "kyl": 1}])
        body["content"]["encoding"] = "base64"
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "not_applicable")
        body["content"]["encoding"] = None
        body["content"]["text"] = "{bad json"
        self.assertEqual(inspect_offline_readonly_shape("/list", "GET", body), "not_applicable")

    def test_existing_har_audit_formats_only_counts(self):
        entries = [
            entry("/candidate", response([{"kcid": "PRIVATE-COURSE", "skbj": "PRIVATE-CLASS", "kyl": 2}])),
            entry("/unfinished", response([{"kcid": "X", "skbj": "Y", "rl": 2}])),
            entry("/hsjw/wap/mycourseschedule.action", response([{"kcid": "X", "skbj": "Y", "kyl": 2}])),
            entry("/catalog", response([{"kcid": "X", "skbj": "Y", "kyl": 2}], extra={"prohibit": "1"})),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.har"
            path.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
            result = summarize_har(path)
        self.assertEqual(result.catalog_json_candidate_signals, 1)
        self.assertEqual(result.catalog_fields_incomplete_signals, 1)
        self.assertEqual(result.catalog_timetable_exclusions, 1)
        self.assertEqual(result.catalog_window_exclusions, 1)
        formatted = format_summary(result)
        for sensitive in ("FAKE_SECRET", "PRIVATE-COURSE", "PRIVATE-CLASS", "PRIVATE_NAME", "/candidate"):
            self.assertNotIn(sensitive, formatted)
        self.assertIn("仅是离线结构线索", formatted)
        self.assertIn("1 条", formatted)


if __name__ == "__main__":
    unittest.main()