"""Offline-only tests for verified in-EXE form login and credential handling."""
import asyncio
from contextlib import asynccontextmanager
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx

from core.client import AuthenticationError, KingoClient
from core.native_login import NativeLoginResult, login_with_verified_form, validate_login_contract


def config():
    return {
        "base_url": "https://jwmis.ncwu.edu.cn",
        "protocol": {
            "verified": True, "identity_path": "/me",
            "identity_referer": "/", "identity_selector": "#me",
        },
        "auth": {
            "cookie": "",
            "login": {
                "verified": True, "method": "POST", "path": "/login",
                "username_field": "account", "password_field": "secret",
                "static_fields": {"mode": "student"},
            },
        },
    }


class Budget:
    def __init__(self):
        self.requests = 0
        self.cooldowns = []

    @asynccontextmanager
    async def request(self):
        self.requests += 1
        yield

    async def impose_retry_after(self, seconds):
        self.cooldowns.append(seconds)


class NativeLoginTests(TestCase):
    def do_login(self, cfg, handler, identity=None, username="alice", password="private"):
        budget = Budget()
        receiver = Mock()
        receiver.identity = AsyncMock(return_value="alice")
        receiver.aclose = AsyncMock()
        if identity is not None:
            receiver.identity.side_effect = identity

        def identity_factory(c, *, session_cookie):
            self.assertNotIn("private", repr(c))
            receiver.received_cookie = session_cookie
            return receiver

        def transport_factory():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

        with patch("core.native_login.build_verified_budget", return_value=budget):
            result = asyncio.run(login_with_verified_form(
                cfg, username, password, transport_factory=transport_factory,
                identity_factory=identity_factory,
            ))
        return result, budget, receiver

    def test_unverified_contract_never_instantiates_transport(self):
        cfg = config()
        cfg["auth"]["login"]["verified"] = False
        self.assertEqual(validate_login_contract(cfg), "login_unverified")
        with patch("core.native_login.build_verified_budget") as budget:
            result = asyncio.run(login_with_verified_form(cfg, "alice", "private"))
        self.assertEqual(result.code, "login_unverified")
        budget.assert_not_called()

    def test_external_endpoint_and_malformed_contract_rejected(self):
        cfg = config()
        cfg["auth"]["login"]["path"] = "//evil.example/login"
        self.assertEqual(validate_login_contract(cfg), "invalid_contract")
        cfg["auth"]["login"]["path"] = "/login"
        cfg["auth"]["login"]["password_field"] = "account"
        self.assertEqual(validate_login_contract(cfg), "invalid_contract")

    def test_success_requires_identity_and_stays_off_disk(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, headers={"set-cookie": "SID=token123; Path=/; HttpOnly"},
                                  content=b"ok")
        result, budget, receiver = self.do_login(config(), handler)
        self.assertTrue(result.verified)
        self.assertEqual(result.session_cookie, "SID=token123")
        self.assertNotIn("token123", repr(result))
        self.assertNotIn("token123", result.message)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertIn(b"account=alice", requests[0].content)
        self.assertIn(b"secret=private", requests[0].content)
        self.assertEqual(budget.requests, 1)
        self.assertEqual(receiver.received_cookie, "SID=token123")
        receiver.identity.assert_awaited_once()
        receiver.aclose.assert_awaited_once()

    def test_401_failure_does_not_return_cookie(self):
        result, budget, receiver = self.do_login(
            config(), lambda r: httpx.Response(401, headers={"set-cookie": "SID=bad"}))
        self.assertEqual(result.code, "expired")
        self.assertFalse(result.verified)
        receiver.identity.assert_not_awaited()

    def test_429_respects_cooldown_and_never_retries(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(429, headers={"retry-after": "120"})
        result, budget, receiver = self.do_login(config(), handler)
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.cooldowns, [120.0])
        receiver.identity.assert_not_awaited()

    def test_cross_origin_redirect_does_not_follow(self):
        result, budget, receiver = self.do_login(
            config(),
            lambda r: httpx.Response(302, headers={"location": "https://other.example/login"}))
        self.assertEqual(result.code, "challenge_required")
        receiver.identity.assert_not_awaited()

    def test_captcha_detected_not_automatically_bypassed(self):
        result, budget, receiver = self.do_login(
            config(), lambda r: httpx.Response(200, content=b"captcha required"))
        self.assertEqual(result.code, "challenge_required")
        receiver.identity.assert_not_awaited()

    def test_identity_rejection_invalidates_cookie(self):
        result, budget, receiver = self.do_login(
            config(),
            lambda r: httpx.Response(200, headers={"set-cookie": "SID=token123; Path=/"}),
            identity=AuthenticationError("token123 private body"))
        self.assertEqual(result.code, "expired")
        self.assertFalse(result.session_cookie)
        receiver.aclose.assert_awaited_once()

    def test_missing_request_budget_blocks_network(self):
        cfg = config()
        with patch("core.native_login.build_verified_budget", side_effect=ValueError("policy")):
            result = asyncio.run(login_with_verified_form(cfg, "alice", "private"))
        self.assertEqual(result.code, "policy_unverified")

    def test_explicit_volatile_cookie_overrides_environment(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "protocol": {"verified": True}, "auth": {"cookie": "SID=legacy"}}
        with patch.dict("os.environ", {"KINGO_COOKIE": "SID=environment"}):
            client = KingoClient(cfg, offline_test_mode=True, session_cookie="SID=volatile")
        try:
            self.assertEqual(client.http.cookies.get("SID"), "volatile")
        finally:
            asyncio.run(client.aclose())


if __name__ == "__main__":
    import unittest
    unittest.main()
