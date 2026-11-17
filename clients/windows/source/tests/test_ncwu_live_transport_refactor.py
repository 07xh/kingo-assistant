"""Preserve the current S202 catalog's exact transport and error behavior."""
import asyncio
from contextlib import asynccontextmanager
from unittest import TestCase

import httpx

from core.ncwu_live_elective import live_s202_snapshot

CONFIG = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=TEST_ONLY_NOT_A_REAL_SESSION"


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


def run(handler, *, config=None, budget_factory=None):
    requests = []
    def handler_record(request):
        requests.append(request)
        return handler(request)
    kwargs = {
        "session_cookie": COOKIE,
        "http_factory": lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler_record),
            follow_redirects=False,
        ),
        "enforce_manual_cooldown": False,
    }
    if budget_factory:
        kwargs["budget_factory"] = budget_factory
    result = asyncio.run(live_s202_snapshot(config or CONFIG, **kwargs))
    return result, requests


class S202LiveTransportRefactorTests(TestCase):
    def test_verified_budget_applies_to_each_request_and_stops_on_429(self):
        budget = Budget()
        factory_calls = []
        def factory(config, origin):
            factory_calls.append((config, origin))
            return budget

        def reply(request):
            self.assertEqual(request.url.host, "jwmis.ncwu.edu.cn")
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/hsjw/student/wsxk.zx.html")
            self.assertIn("TEST_ONLY", request.headers["cookie"])
            return httpx.Response(429, headers={"Retry-After": "75"})

        cfg = {**CONFIG, "server_policy": {"verified": True}}
        result, captured = run(reply, config=cfg, budget_factory=factory)
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(captured), 1)
        self.assertEqual(budget.requests, 1)
        self.assertEqual(budget.after, [75.0])
        self.assertEqual(factory_calls, [(cfg, ("https", "jwmis.ncwu.edu.cn"))])

    def test_unverified_policy_never_invokes_budget_factory(self):
        def budget_factory(*_):
            self.fail("Unverified policy must not initialize budget")
        result, captured = run(
            lambda req: httpx.Response(403),
            budget_factory=budget_factory,
        )
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(captured), 1)

    def test_invalid_verified_budget_fails_before_any_school_request(self):
        def budget_factory(*_):
            raise ValueError("private policy configuration")
        result, captured = run(
            lambda req: self.fail("Request must not be sent"),
            config={**CONFIG, "server_policy": {"verified": True}},
            budget_factory=budget_factory,
        )
        self.assertEqual(result.code, "policy_unverified")
        self.assertEqual(captured, [])
        self.assertNotIn("private", result.message.lower())

    def test_200_empty_page_remains_a_page_protocol_error(self):
        result, captured = run(lambda req: httpx.Response(200, content=b""))
        self.assertEqual(result.code, "protocol_changed")
        self.assertEqual(result.stage, "page")
        self.assertEqual(len(captured), 1)

    def test_200_oversized_page_remains_a_page_protocol_error(self):
        result, captured = run(
            lambda req: httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
        )
        self.assertEqual(result.code, "protocol_changed")
        self.assertEqual(len(captured), 1)

    def test_off_origin_redirect_never_followed(self):
        result, captured = run(
            lambda req: httpx.Response(
                302, headers={"Location": "https://unrelated.example/"}),
        )
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(captured), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
