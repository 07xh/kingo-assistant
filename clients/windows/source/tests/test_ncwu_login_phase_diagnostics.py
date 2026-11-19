"""Offline-only first-party login phase diagnostics, no live NCWU access."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from core.native_login import NativeLoginResult
from core.ncwu_signed_login import login_ncwusigned


CONFIG = {"base_url": "https://jwmis.ncwu.edu.cn"}
USER = "synthetic-user"
PASSWORD = "synthetic-password"


class LoginStageTests(unittest.IsolatedAsyncioTestCase):
    async def _login(self, handler):
        def factory():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                     follow_redirects=False, trust_env=False)
        return await login_ncwusigned(
            CONFIG, USER, PASSWORD,
            http_factory=factory, enforce_local_cooldown=False,
        )

    async def test_rate_limit_reports_login_page_without_retry_or_secret(self):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            return httpx.Response(429, headers={"Retry-After": "120"})
        result = await self._login(handler)
        self.assertEqual(result.code, "restricted")
        self.assertEqual(result.stage, "login_page")
        self.assertIn("登录页读取", result.message)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(PASSWORD, result.message)
        self.assertNotIn(USER, result.message)
        self.assertFalse(result.verified)

    async def test_script_change_is_not_misreported_as_bad_credentials(self):
        calls = []
        def handler(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/cas/login.action"):
                return httpx.Response(200, text="<html>synthetic</html>",
                                      headers={"content-type": "text/html"})
            return httpx.Response(200, text="no supported deskey",
                                  headers={"content-type": "text/javascript"})
        with patch("core.ncwu_signed_login._form_bootstrap",
                   return_value=("a"*32, "b"*32)):
            result = await self._login(handler)
        self.assertEqual(result.code, "protocol_changed")
        self.assertEqual(result.stage, "signing_script")
        self.assertIn("签名脚本读取", result.message)
        self.assertEqual(len(calls), 2)
        self.assertFalse(result.session_cookie)

    async def test_wrong_identity_is_distinguished_from_login_transport(self):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            path = request.url.path
            if request.method == "POST" and path.endswith("/showYearTerm.action"):
                return httpx.Response(
                    200,
                    json={"xn": "2026", "xqM": "0",
                          "xqName": "第一学期", "jwUserYeartermId": "synthetic",
                          "userCode": "not-the-account"},
                    headers={"content-type": "application/json"},
                )
            if path.endswith("/cas/login.action"):
                return httpx.Response(200, text="<html>synthetic</html>",
                                      headers={"content-type": "text/html"})
            if path.endswith("/SetKingoEncypt.jsp"):
                return httpx.Response(200, text="var _deskey='abcdefgh';",
                                      headers={"content-type": "text/javascript"})
            if request.method == "GET":
                return httpx.Response(200, text="synthetic",
                                      headers={"content-type": "text/plain"})
            return httpx.Response(200, text="synthetic",
                                  headers={"content-type": "text/plain"})

        with (patch("core.ncwu_signed_login._form_bootstrap",
                    return_value=("a"*32, "b"*32)),
              patch("core.ncwu_signed_login._sign_login_form",
                    return_value={"username": USER}),
              patch("core.ncwu_signed_login._school_response",
                    return_value="ok")):
            result = await self._login(handler)
        self.assertEqual(result.code, "identity_mismatch")
        self.assertEqual(result.stage, "identity_readback")
        self.assertIn("官方账号身份回读", result.message)
        self.assertEqual(len(calls), 6)
        self.assertEqual(sum(method == "POST" for method, _ in calls), 2)
        self.assertNotIn("not-the-account", result.message)
        self.assertFalse(result.verified)
        self.assertFalse(result.session_cookie)

    async def test_captcha_requirement_fails_closed_at_correct_stage(self):
        with patch("core.ncwu_signed_login._form_bootstrap",
                   side_effect=PermissionError("verification required")):
            result = await self._login(
                lambda request: httpx.Response(200, text="<html>synthetic</html>",
                    headers={"content-type": "text/html"})
            )
        self.assertEqual(result.code, "challenge_required")
        self.assertEqual(result.stage, "login_page")
        self.assertFalse(result.verified)


class PhasePrivacyTests(unittest.TestCase):
    def test_only_allowlisted_diagnostic_labels_are_exposed(self):
        result = NativeLoginResult(
            "unavailable", stage="forged-stage-PRIVATE-TOKEN"
        )
        self.assertEqual(result.diagnostic_stage, "")
        self.assertNotIn("PRIVATE-TOKEN", result.message)

    def test_success_state_never_implies_enrollment(self):
        result = NativeLoginResult("verified", "SECRET-COOKIE",
                                   "jwmis.ncwu.edu.cn", stage="complete")
        self.assertTrue(result.verified)
        self.assertIn("不代表可自动提交选课", result.message)
        self.assertNotIn("SECRET-COOKIE", repr(result))
        self.assertNotIn("SECRET-COOKIE", result.message)


if __name__ == "__main__":
    unittest.main()
