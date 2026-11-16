"""Synthetic HAR only; no school/network I/O."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.auth_route_audit import summarize_auth_route_har


def item(url, method="GET", status=200, headers=None):
    return {
        "request": {"url": url, "method": method, "headers": [
            {"name": "Cookie", "value": "SID=SYNTHETIC-SECRET"}
        ], "postData": {"text": "password=SYNTHETIC-SECRET"}},
        "response": {"status": status, "headers": headers or [], "content": {
            "text": "token=SYNTHETIC-SECRET"
        }},
    }


def analyze(entries):
    with TemporaryDirectory() as d:
        path = Path(d) / "capture.har"
        path.write_text(json.dumps({"log": {"entries": entries}}, ensure_ascii=False), encoding="utf-8")
        return summarize_auth_route_har(path)


class AuthRouteAuditTests(TestCase):
    def test_cas_to_school_service_detected_without_exposing_credentials(self):
        from urllib.parse import quote
        service = quote("https://jwmis.ncwu.edu.cn/hsjw/caslogin", safe="")
        auth_url = "https://authserver.ncwu.edu.cn/authserver/login?service=" + service
        summary = analyze([
            item(auth_url),
            item(auth_url, "POST"),
            item("https://jwmis.ncwu.edu.cn/hsjw/caslogin?ticket=SYNTHETIC-SECRET"),
            item("https://unrelated.example/callback?secret=SYNTHETIC-SECRET"),
        ])
        self.assertEqual(summary.observed_route, "cas_observed")
        self.assertEqual(summary.cas_auth_post_requests, 1)
        self.assertEqual(summary.cas_login_page_requests, 2)
        self.assertEqual(summary.cas_school_service_links, 2)
        self.assertEqual(summary.school_cas_callback_requests, 1)
        self.assertEqual(summary.non_school_entries_ignored, 1)
        self.assertNotIn("SYNTHETIC-SECRET", repr(summary))
        self.assertNotIn("SYNTHETIC-SECRET", summary.message)
        self.assertNotIn("ticket", summary.message.lower())
        self.assertNotIn("password", summary.message.lower())

    def test_school_redirect_onto_cas_counted_as_link(self):
        from urllib.parse import quote
        dest = ("https://authserver.ncwu.edu.cn/authserver/login?service="
                + quote("https://jwmis.ncwu.edu.cn/hsjw/caslogin", safe=""))
        summary = analyze([item(
            "https://jwmis.ncwu.edu.cn/",
            status=302, headers=[{"name": "Location", "value": dest}]
        )])
        self.assertEqual(summary.cas_school_service_links, 1)
        self.assertEqual(summary.observed_route, "cas_observed")

    def test_lookalike_domains_and_http_excluded(self):
        summary = analyze([
            item("https://authserver.ncwu.edu.cn.evil.example/authserver/login"),
            item("https://evil.example/?next=https://jwmis.ncwu.edu.cn"),
            item("http://authserver.ncwu.edu.cn/authserver/login"),
            item("https://jwmis.ncwu.edu.cn.evil.example/hsjw/caslogin"),
        ])
        self.assertEqual(summary.observed_route, "no_evidence")
        self.assertEqual(summary.non_school_entries_ignored, 4)

    def test_school_homepage_is_not_login_proof(self):
        summary = analyze([item("https://jwmis.ncwu.edu.cn/")])
        self.assertEqual(summary.observed_route, "school_only")
        self.assertIn("不能确定", summary.message)

    def test_invalid_service_target_is_never_accepted(self):
        from urllib.parse import quote
        for value in (
            "https://jwmis.ncwu.edu.cn.evil.example/hsjw/caslogin",
            "http://jwmis.ncwu.edu.cn/hsjw/caslogin",
            "https://jwmis.ncwu.edu.cn:1234/hsjw/caslogin",
        ):
            summary = analyze([item(
                "https://authserver.ncwu.edu.cn/authserver/login?service="
                + quote(value, safe="")
            )])
            self.assertEqual(summary.cas_school_service_links, 0)

    def test_file_size_bound_and_syntax_error(self):
        with TemporaryDirectory() as d:
            p = Path(d) / "bad.har"
            p.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(ValueError):
                summarize_auth_route_har(p)
            p.write_text(json.dumps({"log": {"entries": [{}] * 3001}}))
            with self.assertRaises(ValueError):
                summarize_auth_route_har(p)


if __name__ == "__main__":
    import unittest
    unittest.main()
