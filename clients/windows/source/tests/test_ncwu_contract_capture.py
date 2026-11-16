"""Synthetic HAR only: no school HTTP and no sensitive value exposure."""
import json
from unittest import TestCase

from core.ncwu_contract_capture import (
    INIT_PATH, ELIGIBILITY_PATH, inspect_two_endpoint_contracts,
)


def make_har():
    return json.dumps({"log": {"entries": [
        {
            "request": {
                "url": "https://jwmis.ncwu.edu.cn" + INIT_PATH,
                "method": "POST",
                "headers": [{"name": "Cookie",
                             "value": "JSESSIONID=KEEP_SECRET"}],
                "postData": {
                    "mimeType": "application/x-www-form-urlencoded",
                    "text": "xh=PRIVATE_STUDENT&xn=2026&lcid=PRIVATE_ROUND",
                },
            },
            "response": {
                "status": 200, "content": {
                    "text": json.dumps({"status": "200",
                        "result": json.dumps({
                            "lcid": "PRIVATE_ROUND", "xh": "PRIVATE_STUDENT",
                            "zydm": "PRIVATE_MAJOR"})})
                },
            },
        },
        {
            "request": {
                "url": "https://jwmis.ncwu.edu.cn" + ELIGIBILITY_PATH,
                "method": "POST",
                "postData": {"params": [
                    {"name": "skbjdm", "value": "PRIVATE_CLASS"},
                    {"name": "xh", "value": "PRIVATE_STUDENT"},
                ]},
            },
            "response": {
                "status": 200, "content": {"text":
                    json.dumps({"status": "400", "message": "PRIVATE_REASON"})}
            },
        },
        {
            "request": {
                "url": "https://jwmis.ncwu.edu.cn.evil.org" + INIT_PATH,
                "method": "POST",
                "postData": {"params": [
                    {"name": "secret_field", "value": "MUST_NOT_MATCH"}]},
            },
            "response": {"status": 200},
        },
    ]}}, ensure_ascii=False).encode()


class ContractCaptureTests(TestCase):
    def test_only_exact_ncwu_first_party_actions_are_reported(self):
        report = inspect_two_endpoint_contracts(make_har())
        self.assertEqual(report.code, "parsed")
        self.assertEqual(len(report.samples), 2)
        init, elig = report.samples
        self.assertEqual(init.request_count, 1)
        self.assertEqual(elig.request_count, 1)
        self.assertEqual(init.request_fields, ("lcid", "xh", "xn"))
        self.assertEqual(init.result_fields, ("lcid", "xh", "zydm"))
        self.assertEqual(elig.request_fields, ("skbjdm", "xh"))
        self.assertEqual(elig.response_fields, ("message", "status"))
        self.assertNotIn("secret_field", report.message)

    def test_secrets_never_leave_original_har(self):
        report = inspect_two_endpoint_contracts(make_har())
        display = repr(report) + report.message
        for secret in (
            "PRIVATE_STUDENT", "PRIVATE_CLASS", "PRIVATE_ROUND",
            "PRIVATE_MAJOR", "PRIVATE_REASON", "KEEP_SECRET",
        ):
            self.assertNotIn(secret, display)

    def test_no_matching_samples_remain_unverified(self):
        data = json.dumps({"log": {"entries": []}}).encode()
        report = inspect_two_endpoint_contracts(data)
        self.assertEqual(report.code, "parsed")
        self.assertFalse(report.samples[0].captured)
        self.assertFalse(report.samples[0].has_request_contract)
        self.assertIn("未获得", report.message)

    def test_missing_body_does_not_invent_parameters(self):
        har = json.dumps({"log": {"entries": [{
            "request": {"url": "https://jwmis.ncwu.edu.cn" + INIT_PATH,
                        "method": "POST"}, "response": {"status": 200},
        }]}}).encode()
        result = inspect_two_endpoint_contracts(har)
        self.assertTrue(result.samples[0].captured)
        self.assertFalse(result.samples[0].has_request_contract)

    def test_invalid_or_oversize_har_fails_closed(self):
        self.assertEqual(inspect_two_endpoint_contracts(b"hello").code, "invalid")
        self.assertEqual(inspect_two_endpoint_contracts(b"x" * (16*1024*1024+1)).code, "invalid")

    def test_get_does_not_count_as_post_evidence(self):
        data = json.dumps({"log": {"entries": [{
            "request": {"url": "https://jwmis.ncwu.edu.cn" + ELIGIBILITY_PATH,
                        "method": "GET"},
            "response": {"status": 200},
        }]}}).encode()
        result = inspect_two_endpoint_contracts(data)
        self.assertEqual(result.samples[1].request_count, 0)

    def test_base64_body_does_not_report_response_field_values(self):
        data = json.dumps({"log": {"entries": [{
            "request": {
                "url": "https://jwmis.ncwu.edu.cn" + INIT_PATH,
                "method": "POST",
                "postData": {"text": "PRIVATE_SECRET",
                             "mimeType": "application/octet-stream"},
            },
            "response": {"status": 200, "content": {
                "encoding": "base64", "text": "PRIVATE_SECRET"}},
        }]}}).encode()
        result = inspect_two_endpoint_contracts(data)
        self.assertFalse(result.samples[0].has_request_contract)
        self.assertEqual(result.samples[0].response_fields, ())
        self.assertNotIn("PRIVATE_SECRET", result.message)


if __name__ == "__main__":
    import unittest
    unittest.main()
