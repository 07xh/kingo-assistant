"""Synthetic, no-network tests for NCWU timetable evidence classification."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.har_audit import summarize_har
from core.ncwu_timetable_schema import (TimetableSchemaError,
                                        inspect_timetable_json)


def timetable():
    result = {"xn": "2026-2027", "xq": "1", "zc": "10", "sjhjinfo": []}
    result.update({f"week{index}": [] for index in range(1, 8)})
    return result


class TimetableSchemaTest(unittest.TestCase):
    def test_valid_shape_is_not_a_course_catalog(self):
        obj = timetable()
        obj["week1"] = [{"course_name": "PRIVATE_COURSE", "student": "PRIVATE_PERSON"}]
        obj["zxinfo"] = [{"zxxq": [{"kex": 9, "yixuan": 1}]}]
        summary = inspect_timetable_json(json.dumps(obj, ensure_ascii=False))
        self.assertEqual(summary.weekday_entries, 1)
        self.assertEqual(summary.auxiliary_entries, 0)
        self.assertEqual(summary.source_kind, "personal_timetable_only")
        self.assertFalse(summary.elective_catalog_verified)
        self.assertFalse(summary.seat_availability_verified)
        self.assertNotIn("PRIVATE", repr(summary))

    def test_all_days_required(self):
        obj = timetable()
        del obj["week7"]
        with self.assertRaises(TimetableSchemaError):
            inspect_timetable_json(json.dumps(obj))

    def test_invalid_day_or_auxiliary_shape(self):
        for field, bad in (("week1", {}), ("week2", None), ("sjhjinfo", ""),
                           ("zc", 1), ("xn", None)):
            with self.subTest(field=field):
                obj = timetable()
                obj[field] = bad
                with self.assertRaises(TimetableSchemaError):
                    inspect_timetable_json(json.dumps(obj))

    def test_duplicate_keys_even_nested_are_rejected(self):
        obj = timetable()
        obj["week1"] = [{"duplicate": 1}]
        raw = json.dumps(obj).replace('"duplicate": 1',
                                      '"duplicate": 1, "duplicate": 2')
        with self.assertRaises(TimetableSchemaError):
            inspect_timetable_json(raw)

    def test_nonstandard_and_oversized_payloads_rejected(self):
        for raw in ('{"xn": NaN}', '[]', '{bad}', 'x' * (1024 * 1024 + 1)):
            with self.subTest(sample=raw[:20]):
                with self.assertRaises(TimetableSchemaError):
                    inspect_timetable_json(raw)

    def test_large_arrays_rejected_without_emitting_content(self):
        obj = timetable()
        obj["week3"] = ["PRIVATE"] * 4097
        with self.assertRaises(TimetableSchemaError) as caught:
            inspect_timetable_json(json.dumps(obj))
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_har_counts_timetable_but_not_malformed_schema(self):
        good = timetable()
        good["week2"] = [{"private_record": "PRIVATE_PERSON"}]
        bad = timetable()
        bad["week5"] = {"malformed": True}
        def entry(raw):
            return {
                "request": {
                    "method": "POST",
                    "url": "https://jwmis.ncwu.edu.cn/hsjw//wap/mycourseschedule.action",
                },
                "response": {
                    "status": 200,
                    "content": {"mimeType": "application/json", "text": raw},
                },
            }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.har"
            path.write_text(json.dumps({"log": {"entries": [
                entry(json.dumps(good)), entry(json.dumps(bad))
            ]}}), encoding="utf-8")
            summary = summarize_har(path)
        self.assertEqual(summary.matching_school_origin, 2)
        self.assertEqual(summary.ncwu_timetable_schema_matches, 1)
        self.assertNotIn("PRIVATE", repr(summary))

    def test_har_rejects_encoded_or_spoofed_status(self):
        obj = timetable()
        def entry(status, encoding):
            return {
                "request": {
                    "method": "POST",
                    "url": "https://jwmis.ncwu.edu.cn/hsjw/wap/mycourseschedule.action",
                },
                "response": {
                    "status": status,
                    "content": {"text": json.dumps(obj), "encoding": encoding},
                },
            }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.har"
            path.write_text(json.dumps({"log": {"entries": [
                entry(True, None), entry(200, "base64")
            ]}}), encoding="utf-8")
            summary = summarize_har(path)
        self.assertEqual(summary.ncwu_timetable_schema_matches, 0)


if __name__ == "__main__":
    unittest.main()
