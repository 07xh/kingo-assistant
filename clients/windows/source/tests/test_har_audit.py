import json
from pathlib import Path
import tempfile
import unittest
from core.har_audit import summarize_har, format_summary


class HarAuditTest(unittest.TestCase):
    def test_only_school_https_and_zero_request_contents_leak(self):
        entries = [
            {"request":{"url":"https://jwmis.ncwu.edu.cn/private/CODE123?k=SECRET_TOKEN", "method":"GET", "headers":[{"name":"Cookie", "value":"SECRET_COOKIE"}]},
             "response":{"status":200,"content":{"mimeType":"text/html; charset=GBK", "text":"PRIVATE_NAME"}}},
            {"request":{"url":"https://authserver.ncwu.edu.cn/caslogin?token=PRIVATE_ID", "method":"POST", "postData":{"text":"PRIVATE_PASSWORD"}},
             "response":{"status":403,"content":{"mimeType":"application/json"}}},
            {"request":{"url":"https://ncwu.edu.cn.evil.example/", "method":"GET"},
             "response":{"status":200,"content":{"mimeType":"text/html"}}},
            {"request":{"url":"http://jwmis.ncwu.edu.cn/", "method":"GET"},
             "response":{"status":200,"content":{"mimeType":"text/html"}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"sample.har"
            p.write_text(json.dumps({"log":{"entries":entries}}),encoding="utf-8")
            result=summarize_har(p)
            text=format_summary(result)
            self.assertEqual(result.entries_total,4)
            self.assertEqual(result.matching_school_origin,2)
            self.assertEqual(result.method_counts,{"GET":1,"POST":1})
            self.assertEqual(result.status_counts,{"200":1,"403":1})
            for secret in ("SECRET_TOKEN","SECRET_COOKIE","PRIVATE_NAME","PRIVATE_ID","PRIVATE_PASSWORD","CODE123"):
                self.assertNotIn(secret,text)

    def test_invalid_har_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"sample.har"
            p.write_text('{"log": {"entries": "wrong"}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                summarize_har(p)


if __name__ == "__main__":
    unittest.main()
