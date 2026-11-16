"""Only structural HAR evidence leaves this local parser, including base64."""
import base64
import json
from unittest import TestCase

from core.ncwu_contract_capture import (
    INIT_PATH, ELIGIBILITY_PATH, inspect_two_endpoint_contracts,
)

HOST = "https://jwmis.ncwu.edu.cn"
SECRETS = (
    "PRIVATE_SESSION", "PRIVATE_STUDENT_NUMBER",
    "PRIVATE_CLASS_ID", "PRIVATE_RESPONSE_TEXT",
)


def bundle(entries):
    return json.dumps({"log": {"entries": entries}}, ensure_ascii=False).encode("utf-8")


def post(path, *, query="", body=None, response=None, query_params=None):
    req = {"url": HOST + path + query, "method": "POST"}
    if body is not None:
        req["postData"] = body
    if query_params is not None:
        req["queryString"] = query_params
    return {"request": req, "response": response or {"status": 200}}


class ContractEvidenceTests(TestCase):
    def test_query_param_names_and_base64_json_shape_only(self):
        result = {"status": "400", "message": "PRIVATE_RESPONSE_TEXT",
                  "result": json.dumps({
                      "canSelect": False, "reason": "PRIVATE_CLASS_ID",
                  })}
        encoded = base64.b64encode(
            json.dumps(result).encode("utf-8")).decode("ascii")
        entry = post(
            ELIGIBILITY_PATH,
            query="?xktype=2&lcid=PRIVATE_SESSION",
            query_params=[
                {"name": "xktype", "value": "2"},
                {"name": "skbjdm", "value": "PRIVATE_CLASS_ID"},
            ],
            body={"mimeType": "application/x-www-form-urlencoded",
                  "text": "xh=PRIVATE_STUDENT_NUMBER&skbjdm=PRIVATE_CLASS_ID"},
            response={"status": 200, "content": {
                "encoding": "base64", "text": encoded,
                "mimeType": "application/json",
            }},
        )
        rep = inspect_two_endpoint_contracts(bundle([entry]))
        item = rep.samples[1]
        self.assertEqual(item.query_fields, ("lcid", "skbjdm", "xktype"))
        self.assertEqual(item.request_fields, ("skbjdm", "xh"))
        self.assertEqual(item.request_formats, ("form",))
        self.assertEqual(item.response_shapes, ("json_object",))
        self.assertEqual(item.result_shapes, ("object",))
        self.assertEqual(item.result_fields, ("canSelect", "reason"))
        self.assertEqual(item.evidence_state, "schema_only")
        for secret in SECRETS:
            self.assertNotIn(secret, rep.message)
            self.assertNotIn(secret, repr(rep))
        self.assertIn("仍未验证业务语义", rep.message)

    def test_query_only_is_not_full_request_and_response(self):
        entry = post(
            INIT_PATH, query="?xktype=2&lcid=PRIVATE_SESSION",
            response={"status": 200, "content": {
                "text": "<html><body>login</body></html>",
                "mimeType": "text/html",
            }},
        )
        rep = inspect_two_endpoint_contracts(bundle([entry]))
        sample = rep.samples[0]
        self.assertTrue(sample.has_request_contract)
        self.assertEqual(sample.request_fields, ())
        self.assertEqual(sample.query_fields, ("lcid", "xktype"))
        self.assertEqual(sample.response_shapes, ("html",))
        self.assertEqual(sample.evidence_state, "missing_structured_response")
        self.assertNotIn("PRIVATE_SESSION", rep.message)

    def test_bad_base64_does_not_leak_or_claim_a_result(self):
        entry = post(INIT_PATH, body={
            "mimeType": "application/x-www-form-urlencoded",
            "params": [{"name": "xh", "value": "PRIVATE_STUDENT_NUMBER"}],
        }, response={
            "status": 200,
            "content": {"encoding": "base64", "text": "PRIVATE_SESSION!!!"},
        })
        rep = inspect_two_endpoint_contracts(bundle([entry]))
        self.assertEqual(rep.samples[0].response_shapes, ("unknown",))
        self.assertEqual(rep.samples[0].evidence_state, "missing_structured_response")
        self.assertNotIn("PRIVATE_SESSION", rep.message)

    def test_mixed_param_sources_keep_schema_without_any_values(self):
        entry = post(
            INIT_PATH,
            body={
                "mimeType": "application/x-www-form-urlencoded; charset=GBK",
                "params": [{"name": "xn", "value": "PRIVATE_SESSION"}],
                "text": "xh=PRIVATE_STUDENT_NUMBER&xn=2026",
            },
            response={"status": 200, "content": {"text":
                json.dumps({"status": "200", "result": []})}},
        )
        rep = inspect_two_endpoint_contracts(bundle([entry]))
        sample = rep.samples[0]
        self.assertEqual(sample.request_fields, ("xh", "xn"))
        self.assertEqual(sample.result_shapes, ("array",))
        self.assertEqual(sample.response_fields, ("result", "status"))
        self.assertNotIn("PRIVATE_STUDENT_NUMBER", rep.message)

    def test_no_capture_returns_actionable_missing_capture(self):
        rep = inspect_two_endpoint_contracts(bundle([]))
        self.assertEqual(rep.samples[0].evidence_state, "missing_capture")
        self.assertIn("缺少正常使用时的对应 POST 请求样本", rep.message)

    def test_crafted_query_values_cannot_escape_as_field_names(self):
        entry = post(
            INIT_PATH,
            query="?PRIVATE_SESSION=1&safe_key=PRIVATE_SESSION",
            body={"mimeType": "text/plain", "text": "PRIVATE_RESPONSE_TEXT"},
            response={"status": 200},
        )
        rep = inspect_two_endpoint_contracts(bundle([entry]))
        # HAR keys are names, so never copy a name embedding a private value
        # into a report unless the capture itself treats it as a field name.
        self.assertIn("safe_key", rep.samples[0].query_fields)
        self.assertNotIn("PRIVATE_RESPONSE_TEXT", rep.message)

    def test_non_object_log_is_invalid(self):
        for raw in [b"[]", b'{"log":[]}', b'{"log":{"entries":{}}}']:
            with self.subTest(raw=raw):
                self.assertEqual(inspect_two_endpoint_contracts(raw).code, "invalid")


if __name__ == "__main__":
    import unittest
    unittest.main()
