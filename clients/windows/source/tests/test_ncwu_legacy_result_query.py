"""Offline regression of first-party current-session selected-results query."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from urllib.parse import parse_qs
import unittest

import httpx

from core.known_school_pages import PAGES, read_known_school_page, validate_known_page_contract
from core.ncwu_legacy_result_query import (
    CurrentResultQueryError, build_current_result_params,
)


USER = "FICTIONAL001"
BASE = "https://jwmis.ncwu.edu.cn"
TERM_PATH = "/hsjw/jw/common/showYearTerm.action"


def official_term(*, user=USER, year="2026", term="0"):
    return json.dumps({
        "xn": year, "xqM": term, "xqName": "第一学期",
        "jwUserYeartermId": "FAKE-TERM", "userCode": user,
    }).encode("utf-8")


def approved_config():
    return {
        "base_url": BASE,
        "xnxq": "2026-2027-1",
        "auth": {"cookie": ""},
        "known_year_term": {
            "verified": True, "identity_binding_verified": True,
            "method": "POST", "path": TERM_PATH,
            "request_mode": "empty", "request_fields": {},
        },
        "known_school_pages": {"selected_results": {
            "verified": True, "query_verified": True,
            "method": "GET", "path": PAGES["selected_results"],
            "query_mode": "current_yearterm_base64",
            "query_fields": {"params": "current_yearterm_base64"},
        }},
    }


class Budget:
    def __init__(self):
        self.calls = 0
        self.backoffs = []

    @asynccontextmanager
    async def request(self):
        self.calls += 1
        yield

    async def impose_retry_after(self, duration):
        self.backoffs.append(duration)


def run(cfg, handler, *, expected=USER, cookie="SESSIONID=FAKE"):
    calls, budget = [], Budget()

    def serve(request):
        calls.append(request)
        return handler(request)

    result = asyncio.run(read_known_school_page(
        cfg, "selected_results", session_cookie=cookie,
        expected_user_code=expected, environment={},
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(serve),
            follow_redirects=False, trust_env=False,
        ),
        budget_factory=lambda config, origin: budget,
    ))
    return result, calls, budget


RESULT_HTML = (
    "<html><table><tr><th>课程</th><th>选课状态</th></tr>"
    "<tr><td>虚构课程</td><td>候选状态</td></tr></table></html>"
)


class CurrentQueryTests(unittest.TestCase):
    def test_historical_js_canonical_base64_scheme(self):
        token = build_current_result_params(
            official_term(), verified_user_code=USER,
            expected_semester="2026-2027-1")
        self.assertEqual(
            base64.b64decode(token).decode("ascii"),
            "xn=2026&xq=0&xh=" + USER,
        )
        self.assertEqual(
            base64.b64encode(base64.b64decode(token)).decode(), token)

    def test_two_budgeted_requests_share_verified_exe_session(self):
        def handler(request):
            if request.url.path == TERM_PATH:
                self.assertEqual(request.method, "POST")
                self.assertFalse(request.content)
                return httpx.Response(
                    200, content=official_term(),
                    headers={"content-type": "text/plain"},
                )
            self.assertEqual(request.url.path, PAGES["selected_results"])
            self.assertEqual(request.method, "GET")
            fields = dict(x.split("=", 1) for x in base64.b64decode(
                request.url.params["params"]).decode().split("&"))
            self.assertEqual(fields, {"xn": "2026", "xq": "0", "xh": USER})
            return httpx.Response(
                200, text=RESULT_HTML,
                headers={"content-type": "text/html; charset=utf-8"},
            )

        result, calls, budget = run(approved_config(), handler)
        self.assertEqual(result.code, "shape_observed")
        self.assertTrue(result.account_bound_query)
        self.assertTrue(result.semester_bound_query)
        self.assertEqual(budget.calls, 2)
        self.assertEqual([v.method for v in calls], ["POST", "GET"])
        self.assertEqual(len({v.headers.get("cookie") for v in calls}), 1)
        self.assertIn("本次查询账号和学期已与官网本次接口返回值对照", result.message)
        self.assertNotIn(USER, result.message)
        self.assertNotIn("params=", str(result))

    def test_mismatched_yearterm_user_stops_before_selected_get(self):
        result, calls, budget = run(
            approved_config(),
            lambda request: httpx.Response(
                200, content=official_term(user="OTHER001"),
                headers={"content-type": "application/json"},
            ),
        )
        self.assertEqual(result.code, "identity_mismatch")
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.calls, 1)

    def test_no_verified_account_or_no_exe_cookie_never_contacts_school(self):
        for expected, cookie in ((None, "SID=FAKE"), (USER, None),
                                 ("INVALID USER", "SID=FAKE")):
            with self.subTest(expected=expected):
                result, calls, budget = run(
                    approved_config(), lambda request: self.fail("network"),
                    expected=expected, cookie=cookie,
                )
                self.assertEqual(result.code, "identity_unverified")
                self.assertEqual(calls, [])
                self.assertEqual(budget.calls, 0)

    def test_redirect_and_429_never_execute_second_request(self):
        for status, headers, expect in (
            (302, {"location": "/hsjw/cas/login.action"}, "expired"),
            (403, {}, "restricted"),
            (429, {"Retry-After": "75"}, "restricted"),
            (503, {}, "unavailable"),
        ):
            with self.subTest(status=status):
                result, calls, budget = run(
                    approved_config(),
                    lambda request: httpx.Response(status, headers=headers),
                )
                self.assertEqual(result.code, expect)
                self.assertEqual(len(calls), 1)
                self.assertEqual(budget.calls, 1)
                if status == 429:
                    self.assertEqual(budget.backoffs, [75.0])

    def test_invalid_json_or_mime_never_executes_second_request(self):
        for body, mime in (
            (b"not-json", "application/json"),
            (official_term(), "text/html"),
            (official_term(year="20X6"), "application/json"),
        ):
            with self.subTest(mime=mime, body=body[:8]):
                result, calls, budget = run(
                    approved_config(),
                    lambda request: httpx.Response(
                        200, content=body, headers={"content-type": mime}),
                )
                self.assertEqual(result.code, "invalid_response")
                self.assertEqual(len(calls), 1)

    def test_wrong_year_or_semester_aborts_before_result_page_get(self):
        for year, code, term_name in (
            ("2025", "0", "第一学期"),
            ("2026", "1", "第二学期"),
            ("2026", "0", "第二学期"),
        ):
            with self.subTest(year=year, code=code, term_name=term_name):
                body = json.loads(official_term(year=year, term=code))
                body["xqName"] = term_name
                result, calls, budget = run(
                    approved_config(),
                    lambda request: httpx.Response(
                        200, json=body,
                        headers={"content-type": "application/json"},
                    ))
                self.assertEqual(result.code, "semester_mismatch")
                self.assertEqual(len(calls), 1)
                self.assertEqual(budget.calls, 1)

    def test_missing_or_second_term_plan_never_queries(self):
        for plan in (None, "", "2026-2027-2",
                     "2026-2028-1", "2026-2027-0",
                     "2019-2020-1"):
            with self.subTest(plan=plan):
                cfg = approved_config()
                cfg["xnxq"] = plan
                # Only the observed first-semester coding is supported.
                if plan == "2019-2020-1":
                    # Syntactically accepted, but the current school term
                    # must still be checked dynamically against the plan.
                    result, calls, budget = run(
                        cfg, lambda request: httpx.Response(
                            200, content=official_term(),
                            headers={"content-type": "application/json"}))
                    self.assertEqual(result.code, "semester_mismatch")
                    self.assertEqual(len(calls), 1)
                else:
                    self.assertEqual(
                        validate_known_page_contract(cfg, "selected_results"),
                        "query_unverified")
                    result, calls, budget = run(
                        cfg, lambda request: self.fail("no network"))
                    self.assertEqual(result.code, "query_unverified")
                    self.assertEqual(calls, [])
                    self.assertEqual(budget.calls, 0)

    def test_captured_query_and_unverified_yearterm_never_send(self):
        for mutation in ("token", "yearterm", "identity", "field"):
            cfg = approved_config()
            selected = cfg["known_school_pages"]["selected_results"]
            if mutation == "token":
                selected["query_mode"] = "captured_token"
            elif mutation == "yearterm":
                cfg["known_year_term"]["verified"] = False
            elif mutation == "identity":
                cfg["known_year_term"]["identity_binding_verified"] = False
            elif mutation == "field":
                selected["query_fields"] = {"params": "HAR_VALUE"}
            self.assertEqual(
                validate_known_page_contract(cfg, "selected_results"),
                "query_unverified",
            )
            result, calls, budget = run(
                cfg, lambda request: self.fail("no network"))
            self.assertEqual(result.code, "query_unverified")
            self.assertEqual(calls, [])
            self.assertEqual(budget.calls, 0)

    def test_does_not_expose_user_data_in_bad_response_exception(self):
        with self.assertRaises(CurrentResultQueryError) as raised:
            build_current_result_params(
                official_term(user="SENSITIVE_ACCOUNT_VALUE"),
                verified_user_code=USER,
                expected_semester="2026-2027-1",
            )
        self.assertEqual(raised.exception.code, "identity_mismatch")
        self.assertNotIn("SENSITIVE_ACCOUNT_VALUE", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
