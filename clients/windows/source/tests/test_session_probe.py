"""Offline contract tests: no network, browser-cookie import or school write."""
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock

from core.client import AuthenticationError, RateLimitError
from core.session_probe import probe_own_session


class SessionProbeTests(TestCase):
    def setUp(self):
        self.config = {"protocol": {"verified": True}, "auth": {"cookie": ""}}
        self.client = type("FakeClient", (), {})()
        self.client.identity = AsyncMock(return_value="redacted")
        self.client.aclose = AsyncMock()
        self.factory_calls = []

    def factory(self, config):
        self.factory_calls.append(config)
        return self.client

    def probe(self, **kwargs):
        return asyncio.run(probe_own_session(
            self.config, factory=self.factory, environment=kwargs.pop("environment", {}),
            **kwargs))

    def test_unverified_protocol_performs_no_io(self):
        self.config["protocol"]["verified"] = False
        report = self.probe(environment={"KINGO_COOKIE": "TOP_SECRET"})
        self.assertEqual(report.code, "protocol_unverified")
        self.assertFalse(self.factory_calls)

    def test_browser_login_is_not_mistaken_for_script_session(self):
        report = self.probe()
        self.assertEqual(report.code, "missing_session")
        self.assertFalse(self.factory_calls)

    def test_verified_identity_is_only_script_session_not_enrollment(self):
        report = self.probe(environment={"KINGO_COOKIE": "TOP_SECRET"})
        self.assertTrue(report.verified)
        self.assertEqual(report.code, "verified")
        self.client.identity.assert_awaited_once()
        self.client.aclose.assert_awaited_once()
        self.assertNotIn("TOP_SECRET", report.message)
        self.assertNotIn("redacted", report.message)

    def test_expired_identity_fails_closed(self):
        self.client.identity.side_effect = AuthenticationError("SECRET_PAGE_BODY")
        report = self.probe(environment={"KINGO_COOKIE": "TOP_SECRET"})
        self.assertEqual(report.code, "expired")
        self.assertFalse(report.verified)
        self.assertNotIn("SECRET_PAGE_BODY", report.message)
        self.client.aclose.assert_awaited_once()

    def test_restriction_does_not_retry_or_escalate(self):
        self.client.identity.side_effect = RateLimitError("blocked")
        report = self.probe(environment={"KINGO_COOKIE": "TOP_SECRET"})
        self.assertEqual(report.code, "restricted")
        self.client.identity.assert_awaited_once()
        self.client.aclose.assert_awaited_once()

    def test_unexpected_failure_is_redacted_and_client_is_closed(self):
        self.client.identity.side_effect = RuntimeError("secret")
        report = self.probe(environment={"KINGO_COOKIE": "TOP_SECRET"})
        self.assertEqual(report.code, "unavailable")
        self.assertNotIn("secret", report.message)
        self.client.aclose.assert_awaited_once()


if __name__ == "__main__":
    import unittest
    unittest.main()
