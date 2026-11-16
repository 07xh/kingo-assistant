"""Synthetic first-party S202 time-window/round probes; no campus traffic."""
import asyncio
import json
from unittest import TestCase

import httpx

from core.ncwu_s202_round import parse_s202_round, probe_s202_round

BASE = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=LOCAL_TEST_SESSION"
PRIVATE_XH = "PRIVATE_STUDENT_FIELD"
PRIVATE_LCID = "PRIVATE_ROUND_FIELD"


def payload(status="400", valid="0"):
    fields = {
        "lx": "2", "xh": PRIVATE_XH, "xn": "2026", "xqM": "0",
        "lcid": PRIVATE_LCID, "nj": "2026", "zydm": "PRIVATE_MAJOR",
        "lcmc": "体育选课测试轮次", "xqName": "第一学期",
        "qssj": "2026-12-20 08:00", "jssj": "2026-12-21 20:00",
        "isValidTimerange": valid,
    }
    message = ("当前时间不是有效的选课时间区段！"
               if status == "400" else "当前时间有效")
    return json.dumps({
        "status": status, "message": message,
        "result": json.dumps(fields, ensure_ascii=False),
    }, ensure_ascii=False).encode("utf-8")


def run(handler, *, cookie=COOKIE, config=None):
    calls = []
    def transport(request):
        calls.append(request)
        return handler(request)
    result = asyncio.run(probe_s202_round(
        config or BASE, session_cookie=cookie,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            follow_redirects=False),
        enforce_manual_cooldown=False,
    ))
    return result, calls


class S202RoundTest(TestCase):
    def test_closed_window_still_reports_safe_round_metadata(self):
        obj = parse_s202_round(payload())
        self.assertEqual(obj.code, "observed")
        self.assertEqual(obj.window, "closed")
        self.assertEqual(obj.field_count, 6)
        self.assertFalse(obj.current_context_complete)
        self.assertIn("尚未开放", obj.message)
        self.assertIn("体育选课测试轮次", obj.message)
        self.assertNotIn(PRIVATE_XH, str(obj))
        self.assertNotIn(PRIVATE_LCID, obj.message)
        self.assertNotIn("PRIVATE_MAJOR", str(obj))

    def test_open_window_with_full_context_remains_only_reported_open(self):
        obj = parse_s202_round(payload("200", "1"))
        self.assertEqual(obj.window, "reported_open")
        self.assertTrue(obj.current_context_complete)
        self.assertIn("未核对课程", obj.message)

    def test_contradictory_window_is_not_promoted(self):
        self.assertEqual(parse_s202_round(
            payload("200", "0")).window, "inconsistent")
        self.assertEqual(parse_s202_round(
            payload("400", "1")).window, "inconsistent")

    def test_endpoint_is_one_bounded_first_party_post(self):
        def handler(req):
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.url.path,
                             "/hsjw/jw/common/getWsxkTimeRange.action")
            self.assertEqual(req.url.query.decode(), "xktype=2")
            self.assertEqual(req.content, b"")
            self.assertEqual(req.url.host, "jwmis.ncwu.edu.cn")
            self.assertIn("LOCAL_TEST_SESSION", req.headers["cookie"])
            return httpx.Response(200, content=payload(),
                                  headers={"content-type": "application/json"})
        status, calls = run(handler)
        self.assertEqual(status.code, "observed")
        self.assertEqual(len(calls), 1)

    def test_cookie_missing_makes_no_request(self):
        status, calls = run(lambda req: self.fail("no HTTP"), cookie="")
        self.assertEqual(status.code, "missing_session")
        self.assertEqual(calls, [])

    def test_impostor_host_makes_no_request(self):
        status, calls = run(lambda req: self.fail("no HTTP"),
                            config={"base_url": "https://jwmis.ncwu.edu.cn.fake.com"})
        self.assertEqual(status.code, "invalid_contract")
        self.assertEqual(calls, [])

    def test_expired_html_http_200_is_rejected(self):
        status, calls = run(lambda req: httpx.Response(
            200, content="凭证已失效".encode("utf-8")))
        self.assertEqual(status.code, "expired")
        self.assertEqual(len(calls), 1)

    def test_redirect_and_429_are_not_retried(self):
        status, calls = run(lambda req: httpx.Response(
            302, headers={"Location": "https://authserver.ncwu.edu.cn"}))
        self.assertEqual(status.code, "expired")
        self.assertEqual(len(calls), 1)
        status, calls = run(lambda req: httpx.Response(
            429, headers={"Retry-After": "120"}))
        self.assertEqual(status.code, "restricted")
        self.assertEqual(len(calls), 1)

    def test_bad_json_and_incomplete_context_fail_closed(self):
        self.assertEqual(parse_s202_round(b"not json").code,
                         "invalid_response")
        no_scope = json.dumps({
            "status": "200", "result": {"lx": "2", "xn": "2026"},
        }).encode()
        obj = parse_s202_round(no_scope)
        self.assertEqual(obj.code, "observed")
        self.assertFalse(obj.current_context_complete)
        self.assertEqual(obj.field_count, 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
