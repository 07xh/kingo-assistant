"""Offline tests of the persistent Lite login/session coordinator.

No school network traffic, password logging, or enrollment write occurs.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import AsyncMock, patch

from core.native_login import NativeLoginResult
from core.ncwu_live_elective import LiveElectiveSnapshot, LiveElectiveCourse
from lite_session import LiteSession


class LiteSessionTests(TestCase):
    def test_session_starts_logged_out(self):
        session = LiteSession()
        self.assertEqual(session.info()["status"], "not_logged_in")
        self.assertFalse(session.info()["persistent"])
        self.assertNotIn("cookie", session.info())

    def test_login_keeps_cookie_inside_private_process(self):
        signed = NativeLoginResult("verified", "JSESSIONID=sensitive-secret", "jwmis.ncwu.edu.cn")
        session = LiteSession()
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock, return_value=signed) as invoke:
            reply = session.login({"base_url": "https://jwmis.ncwu.edu.cn"}, "test-account", "test-password")
        self.assertEqual(invoke.await_count, 1)
        self.assertEqual(reply["code"], "verified")
        self.assertTrue(reply["session"]["verified_at_login"])
        self.assertEqual(session.cookie, "JSESSIONID=sensitive-secret")
        self.assertNotIn("sensitive-secret", str(reply))
        self.assertNotIn("test-password", str(reply))
        self.assertNotIn("cookie", str(reply))

    def test_login_failure_clears_prior_cookie(self):
        session = LiteSession("JSESSIONID=old", "jwmis.ncwu.edu.cn", "verified")
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock,
                   return_value=NativeLoginResult("challenge_required")):
            response = session.login({"base_url": "https://jwmis.ncwu.edu.cn"}, "test", "password")
        self.assertEqual(response["code"], "challenge_required")
        self.assertEqual(session.cookie, "")
        self.assertEqual(session.info()["status"], "not_logged_in")

    def test_catalog_without_session_cannot_call_network(self):
        session = LiteSession()
        with patch("lite_session.live_s202_snapshot", new_callable=AsyncMock) as read:
            result = session.catalog({})
        self.assertEqual(result["code"], "missing_session")
        read.assert_not_called()

    def test_catalog_only_returns_public_course_fields_and_is_limited(self):
        session = LiteSession("JSESSIONID=secret", "jwmis.ncwu.edu.cn", "verified")
        course = LiveElectiveCourse("线性代数", "M101", "01", "教师", "未知", "")
        snap = LiveElectiveSnapshot("catalog_read", "catalog", "closed", (course,) * 220)
        with patch("lite_session.live_s202_snapshot", new_callable=AsyncMock, return_value=snap) as read:
            result = session.catalog({"base_url": "https://jwmis.ncwu.edu.cn"})
        self.assertEqual(read.await_count, 1)
        self.assertEqual(result["total_courses"], 220)
        self.assertEqual(len(result["courses"]), 200)
        self.assertFalse(result["seat_count_verified"])
        self.assertEqual(result["data_source"], "live_school")
        self.assertNotIn("secret", str(result))
        self.assertNotIn("session_cookie", str(result))

    def test_expired_catalog_invalidates_session(self):
        session = LiteSession("JSESSIONID=secret", "jwmis.ncwu.edu.cn", "verified")
        with patch("lite_session.live_s202_snapshot", new_callable=AsyncMock,
                   return_value=LiveElectiveSnapshot("expired")):
            result = session.catalog({})
        self.assertEqual(result["code"], "expired")
        self.assertEqual(session.info()["status"], "not_logged_in")
        self.assertEqual(session.cookie, "")

    def test_logout_does_not_trigger_network(self):
        session = LiteSession("JSESSIONID=secret", "jwmis.ncwu.edu.cn", "verified")
        with patch("lite_session.live_s202_snapshot", new_callable=AsyncMock) as catalog:
            session.clear()
        catalog.assert_not_called()
        self.assertEqual(session.info()["status"], "not_logged_in")
