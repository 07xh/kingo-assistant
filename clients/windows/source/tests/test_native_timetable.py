"""MockTransport-only contract tests for the real HAR-confirmed timetable route."""
import asyncio
from contextlib import asynccontextmanager
import json
from unittest import TestCase
from unittest.mock import patch

import httpx

from core.native_timetable import (
    OBSERVED_TIMETABLE_PATH, TimetableReadResult,
    read_known_timetable, validate_timetable_contract,
)


def config():
    return {
        "base_url": "https://jwmis.ncwu.edu.cn",
        "known_timetable": {
            "verified": True,
            "method": "POST",
            "path": OBSERVED_TIMETABLE_PATH,
            "body_mode": "form",
            "request_fields": {"semester": "2026-2027-1"},
        },
        "auth": {"cookie": ""},
    }


def timetable():
    value = {"xn": "2026-2027", "xq": "1", "zc": "10", "sjhjinfo": []}
    value.update({f"week{i}": [] for i in range(1, 8)})
    value["week2"] = [{"student": "PRIVATE_STUDENT", "course": "PRIVATE_COURSE"}]
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class Budget:
    def __init__(self):
        self.requests = 0
        self.after = []

    @asynccontextmanager
    async def request(self):
        self.requests += 1
        yield

    async def impose_retry_after(self, seconds):
        self.after.append(seconds)


def simulate(cfg, responder, cookie="SID=SELF", *, environment=None):
    requests = []
    budget = Budget()
    def handler(request):
        requests.append(request)
        return responder(request)
    def http_factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False)
    with patch("core.native_timetable.build_verified_budget", side_effect=AssertionError("no default budget call")):
        result = asyncio.run(read_known_timetable(
            cfg, session_cookie=cookie, environment=environment or {},
            http_factory=http_factory,
            budget_factory=lambda config, origin: budget,
        ))
    return result, requests, budget


class NativeTimetableTests(TestCase):
    def test_route_is_real_capture_path_not_elective_catalog(self):
        self.assertEqual(OBSERVED_TIMETABLE_PATH, "/hsjw//wap/mycourseschedule.action")
        result, requests, budget = simulate(
            config(), lambda request: httpx.Response(
                200, content=timetable(), headers={"content-type": "application/json"}))
        self.assertEqual(result.code, "schema_matched")
        self.assertEqual(result.weekday_entries, 1)
        self.assertEqual(result.auxiliary_entries, 0)
        self.assertIn("课表", result.message)
        self.assertIn("不是选课目录", result.message)
        self.assertNotIn("PRIVATE", repr(result) + result.message)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].url.path, OBSERVED_TIMETABLE_PATH)
        self.assertEqual(requests[0].headers.get("cookie"), "SID=SELF")
        self.assertEqual(budget.requests, 1)

    def test_unverified_contract_never_creates_network(self):
        cfg = config()
        cfg["known_timetable"]["verified"] = False
        self.assertEqual(validate_timetable_contract(cfg), "route_unverified")
        result, requests, budget = simulate(
            cfg, lambda req: (_ for _ in ()).throw(AssertionError("network")))
        self.assertEqual(result.code, "route_unverified")
        self.assertEqual(requests, [])
        self.assertEqual(budget.requests, 0)

    def test_missing_own_session_never_contacts_school(self):
        result, reqs, budget = simulate(
            config(), lambda req: httpx.Response(200), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(reqs, [])

    def test_never_accepts_sensitive_fields_or_other_host(self):
        cfg = config()
        cfg["known_timetable"]["request_fields"] = {"token": "should-not-write"}
        self.assertEqual(validate_timetable_contract(cfg), "invalid_contract")
        cfg = config()
        cfg["base_url"] = "https://jwmis.ncwu.edu.cn.evil.example"
        self.assertEqual(validate_timetable_contract(cfg), "invalid_contract")
        cfg = config()
        cfg["known_timetable"]["path"] = "/hsjw/wap/enroll.action"
        self.assertEqual(validate_timetable_contract(cfg), "invalid_contract")

    def test_login_redirect_is_not_a_success(self):
        result, requests, budget = simulate(
            config(), lambda r: httpx.Response(
                302, headers={"location": "https://authserver.ncwu.edu.cn/"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_429_stops_and_respects_cooldown(self):
        result, reqs, budget = simulate(
            config(), lambda r: httpx.Response(
                429, headers={"Retry-After": "90"}))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(reqs), 1)
        self.assertEqual(budget.after, [90.0])

    def test_non_timetable_json_rejected(self):
        result, reqs, budget = simulate(
            config(), lambda r: httpx.Response(
                200, json={"remains": 100, "course": "PRIVATE_COURSE"}))
        self.assertEqual(result.code, "invalid_response")
        self.assertNotIn("PRIVATE", result.message)

    def test_html_login_page_rejected(self):
        result, reqs, budget = simulate(
            config(), lambda r: httpx.Response(
                200, content=b"<html>login</html>",
                headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "invalid_response")

    def test_json_request_mode_supported_when_verified(self):
        cfg = config()
        cfg["known_timetable"]["body_mode"] = "json"
        result, reqs, budget = simulate(
            cfg, lambda r: httpx.Response(200, content=timetable(),
                                          headers={"content-type": "application/json"}))
        self.assertEqual(result.code, "schema_matched")
        self.assertIn("application/json", reqs[0].headers["content-type"])


if __name__ == "__main__":
    import unittest
    unittest.main()
