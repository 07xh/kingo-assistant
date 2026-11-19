"""Synthetic evidence only: inspect S202 HAR without any network calls."""
import json
import unittest

from core.ncwu_submit_evidence import inspect_s202_submit_har
from core.ncwu_submission_transport import SUBMIT_PATH
from core.ncwu_live_elective import SELECTED_PAGE

HOST = "https://jwmis.ncwu.edu.cn"
SECRET = "SHOULD_NEVER_BE_EXPORTED"


def pack(entries):
    return json.dumps({"log": {"entries": entries}}).encode("utf-8")


class SubmitEvidenceTests(unittest.TestCase):
    def test_exact_official_post_extracts_only_names_and_shapes(self):
        source = [
            {"request": {
                "url": HOST + SUBMIT_PATH + "?xktype=2",
                "method": "POST",
                "headers": [{"name": "Cookie", "value": SECRET}],
                "postData": {"mimeType": "application/x-www-form-urlencoded",
                             "text": "xh=" + SECRET + "&items=" + SECRET
                                     + "&hidOption=" + SECRET},
            }, "response": {"status": 200, "content": {
                "text": json.dumps({"status": "200", "message": SECRET}),
            }}},
            {"request": {"url": HOST + SELECTED_PAGE, "method": "GET"},
             "response": {"status": 200, "content": {"text": SECRET}}},
        ]
        report = inspect_s202_submit_har(pack(source))
        self.assertEqual(report.code, "parsed")
        self.assertEqual(report.submit_count, 1)
        self.assertEqual(report.results_page_count, 1)
        self.assertEqual(report.request_fields, ("hidOption", "items", "xh"))
        self.assertEqual(report.response_fields, ("message", "status"))
        self.assertIn("xktype", report.query_fields)
        self.assertNotIn(SECRET, repr(report) + report.message)
        self.assertIn("HTTP 200 不表示选课成功", report.message)

    def test_fake_domain_get_or_nonmatching_action_are_not_evidence(self):
        entries = [
            {"request": {
                "url": "https://jwmis.ncwu.edu.cn.evil.invalid" + SUBMIT_PATH,
                "method": "POST", "postData": {
                    "params": [{"name": "private", "value": SECRET}]}},
             "response": {"status": 200}},
            {"request": {"url": HOST + SUBMIT_PATH, "method": "GET"},
             "response": {"status": 200}},
            {"request": {"url": "http://jwmis.ncwu.edu.cn" + SUBMIT_PATH,
                         "method": "POST"}, "response": {"status": 200}},
        ]
        rep = inspect_s202_submit_har(pack(entries))
        self.assertEqual(rep.submit_count, 0)
        self.assertNotIn("private", rep.message)
        self.assertNotIn(SECRET, rep.message)

    def test_invalid_har_is_not_accepted(self):
        for raw in (b"", b"[]", b'{"log":{"entries":{}}}', b"bad"):
            with self.subTest(raw=raw):
                self.assertEqual(inspect_s202_submit_har(raw).code, "invalid")

    def test_no_sample_cannot_be_reported_as_verified(self):
        report = inspect_s202_submit_har(pack([]))
        self.assertEqual(report.submit_count, 0)
        self.assertEqual(report.request_fields, ())
        self.assertIn("当前传输层预期字段未见", report.message)


if __name__ == "__main__":
    unittest.main()
