"""Native EXE direct-form login -> observed YearTerm account binding (offline)."""
import asyncio
from contextlib import asynccontextmanager
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

import httpx

from core.native_login import login_with_verified_form, validate_login_contract
from core.ncwu_term_probe import TermProbeResult


class LocalBudget:
    requests = 0

    @asynccontextmanager
    async def request(self):
        self.requests += 1
        yield

    async def impose_retry_after(self, seconds):
        pass


def year_term_auth_config():
    return {
        "base_url": "https://jwmis.ncwu.edu.cn",
        # Deliberately no arbitrary 'protocol.identity_path':
        # the identity contract uses the captured YearTerm read.
        "known_year_term": {
            "verified": True,
            "identity_binding_verified": True,
            "method": "POST",
            "path": "/hsjw/jw/common/showYearTerm.action",
            "request_mode": "empty",
        },
        "auth": {"login": {
            "mode": "direct_form", "verified": True,
            "contract_variant": "plain_20261103",
            "identity_mode": "year_term",
            "method": "POST", "path": "/hsjw/cas/logon.action",
            "username_field": "username", "password_field": "password",
            "static_fields": {"loginmethod": "APPROVED_TEST_MODE"},
        }},
    }


class YearTermBoundLoginTests(TestCase):
    def run_login(self, term_code="identity_verified"):
        requests = []
        def network(req):
            requests.append(req)
            return httpx.Response(
                200, headers={"set-cookie": "SID=LOCAL_SESSION; Path=/; HttpOnly"},
                text="OK",
            )
        budget = LocalBudget()
        reader = AsyncMock(return_value=TermProbeResult(term_code, 5))
        identity_factory = Mock(side_effect=AssertionError("unexpected arbitrary identity API"))
        config = year_term_auth_config()
        with patch("core.native_login.build_verified_budget", return_value=budget), \
             patch("core.native_login.probe_known_year_term", reader):
            result = asyncio.run(login_with_verified_form(
                config, "LOCAL_STUDENT", "PRIVATE_PASSWORD",
                transport_factory=lambda: httpx.AsyncClient(
                    transport=httpx.MockTransport(network), follow_redirects=False),
                identity_factory=identity_factory,
            ))
        return result, requests, budget, reader, identity_factory

    def test_identity_verified_by_exact_matching_term_user_code(self):
        self.assertIsNone(validate_login_contract(year_term_auth_config()))
        result, requests, budget, reader, identity_factory = self.run_login()
        self.assertTrue(result.verified)
        self.assertEqual(result.session_origin, "jwmis.ncwu.edu.cn")
        self.assertEqual(result.session_cookie, "SID=LOCAL_SESSION")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertIn(b"username=LOCAL_STUDENT", requests[0].content)
        reader.assert_awaited_once_with(
            year_term_auth_config(), session_cookie="SID=LOCAL_SESSION",
            expected_user_code="LOCAL_STUDENT",
        )
        identity_factory.assert_not_called()
        self.assertNotIn("PRIVATE_PASSWORD", repr(result) + result.message)
        self.assertNotIn("LOCAL_STUDENT", repr(result) + result.message)
        self.assertEqual(budget.requests, 1)

    def test_identity_mismatch_never_keeps_session(self):
        result, requests, budget, reader, identity_factory = self.run_login(
            "identity_mismatch")
        self.assertEqual(result.code, "identity_mismatch")
        self.assertFalse(result.verified)
        self.assertEqual(result.session_cookie, "")
        self.assertEqual(result.session_origin, "")
        self.assertEqual(len(requests), 1)

    def test_year_term_unauthorized_drops_session(self):
        result, _, _, _, _ = self.run_login("expired")
        self.assertEqual(result.code, "expired")
        self.assertFalse(result.verified)

    def test_unverified_year_term_contract_blocks_login_before_password_network(self):
        cfg = year_term_auth_config()
        cfg["known_year_term"]["verified"] = False
        self.assertEqual(validate_login_contract(cfg), "identity_unverified")
        with patch("core.native_login.build_verified_budget") as budget, \
             patch("core.native_login.probe_known_year_term") as term:
            result = asyncio.run(login_with_verified_form(
                cfg, "LOCAL_STUDENT", "PRIVATE_PASSWORD"))
        self.assertEqual(result.code, "identity_unverified")
        budget.assert_not_called()
        term.assert_not_called()

    def test_unverified_authenticated_only_behavior_blocks_password_prompt(self):
        cfg = year_term_auth_config()
        cfg["known_year_term"]["identity_binding_verified"] = False
        self.assertEqual(validate_login_contract(cfg), "identity_unverified")

    def test_invalid_identity_mode_denied(self):
        cfg = year_term_auth_config()
        cfg["auth"]["login"]["identity_mode"] = "no_validation"
        self.assertEqual(validate_login_contract(cfg), "invalid_contract")

    def test_dynamic_signed_login_variant_denied(self):
        cfg = year_term_auth_config()
        cfg["auth"]["login"]["contract_variant"] = "signed_20261114"
        self.assertEqual(validate_login_contract(cfg), "signed_variant_unsupported")


if __name__ == "__main__":
    import unittest
    unittest.main()
