"""No-network S202 selected-count tests, with synthetic identity values."""
import asyncio
import json
from unittest import TestCase
from urllib.parse import parse_qs

import httpx

from core.ncwu_s202_score import (
    _context_from_window,
    _selected_count_from_score,
    read_personal_s202_score,
)

CONFIG = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=TEST_SESSION_ONLY"
SESSION_STUDENT = "TEST_OWN_ACCOUNT"


def window(*, status="400", xh=SESSION_STUDENT, xq="0"):
    payload = {
        "status": status,
        "message": "当前时间不是有效的选课时间区段！",
        "result": json.dumps({
            "xh": xh, "xn": "2026", "xqM": xq,
            "lx": "2", "lcid": "TEST_ROUND",
        }),
    }
    return json.dumps(payload).encode()


def score(*, status="200", yxms="2"):
    return json.dumps({"status": status,
                       "result": json.dumps({"yxms": yxms})}).encode()


def call(respond, *, cookie=COOKIE, config=None):
    seen = []
    def transport(req):
        seen.append(req)
        return respond(req)
    result = asyncio.run(read_personal_s202_score(
        config or CONFIG, session_cookie=cookie,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            follow_redirects=False),
        enforce_manual_cooldown=False,
    ))
    return result, seen


class S202ScoreTests(TestCase):
    def test_closed_window_still_has_authoritative_account_scope(self):
        def handler(req):
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.url.host, "jwmis.ncwu.edu.cn")
            self.assertIn("TEST_SESSION_ONLY", req.headers["cookie"])
            if req.url.path.endswith("/getWsxkTimeRange.action"):
                self.assertEqual(req.url.query.decode(), "xktype=2")
                self.assertEqual(req.content, b"")
                return httpx.Response(200, content=window())
            self.assertTrue(req.url.path.endswith(
                "/getSelectLessonScoreKcsInfo.action"))
            payload = parse_qs(req.content.decode("ascii"), encoding="gbk")
            self.assertEqual(payload, {
                "xn": ["2026"], "xq_m": ["0"], "xh": [SESSION_STUDENT],
            })
            return httpx.Response(200, content=score())
        result, seen = call(handler)
        self.assertEqual(len(seen), 2)
        self.assertEqual(result.code, "count_observed")
        self.assertTrue(result.scope_verified)
        self.assertEqual(result.selected_count, 2)
        self.assertNotIn(SESSION_STUDENT, str(result))
        self.assertNotIn("TEST_SESSION_ONLY", result.message)
        self.assertIn("无法证明", result.message)

    def test_empty_selected_count_is_valid_not_a_target_confirmation(self):
        result, seen = call(lambda req: httpx.Response(
            200, content=window() if req.url.path.endswith("getWsxkTimeRange.action")
            else score(yxms=0)))
        self.assertEqual(result.selected_count, 0)
        self.assertEqual(result.code, "count_observed")

    def test_scope_must_come_from_current_session(self):
        def handler(req):
            return httpx.Response(200, content=window(xh=""))
        result, seen = call(handler)
        self.assertEqual(result.code, "round_unavailable")
        self.assertEqual(len(seen), 1)

    def test_redirect_stops_before_score(self):
        result, seen = call(lambda req: httpx.Response(
            302, headers={"Location": "https://authserver.ncwu.edu.cn"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(seen), 1)

    def test_429_does_not_retry(self):
        result, seen = call(lambda req: httpx.Response(
            429, headers={"Retry-After": "100"}))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(seen), 1)

    def test_http200_invalid_session_stops(self):
        result, seen = call(lambda req: httpx.Response(
            200, content="凭证已失效".encode()))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(seen), 1)

    def test_unrecognized_selected_count_does_not_infer(self):
        result, seen = call(lambda req: httpx.Response(
            200, content=window() if req.url.path.endswith("getWsxkTimeRange.action")
            else score(yxms="not-a-count")))
        self.assertEqual(result.code, "score_unrecognized")
        self.assertIsNone(result.selected_count)
        self.assertEqual(len(seen), 2)

    def test_invalid_origin_rejected_before_http(self):
        result, seen = call(lambda req: self.fail("HTTP called"),
                            config={"base_url": "https://jwmis.ncwu.edu.cn.evil.com"})
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(seen, [])

    def test_missing_cookie_rejected_before_http(self):
        result, seen = call(lambda req: self.fail("HTTP called"), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(seen, [])

    def test_context_rejects_foreign_or_corrupted_scope(self):
        self.assertIsNone(_context_from_window(window(xh="abc&admin=1")))
        self.assertIsNone(_context_from_window(b'{"status":"200","result":{}}'))

    def test_score_parser_accepts_only_bounded_integer_counts(self):
        self.assertIsNone(_selected_count_from_score(score(yxms="2.5")))
        self.assertIsNone(_selected_count_from_score(score(yxms="-1")))
        self.assertIsNone(_selected_count_from_score(score(yxms=101)))
        self.assertEqual(_selected_count_from_score(score(yxms=0)), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
