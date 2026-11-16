"""Offline contracts for EXE-native HTTP readiness; never contact NCWU."""
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, Mock

from core.client import AuthenticationError, CourseItem, RateLimitError
from core.native_preparation import prepare_native_api


def config():
    return {
        "protocol": {
            "verified": True, "identity_path": "/identity",
            "identity_referer": "/", "identity_selector": "#my-user",
            "entry": "/entry", "entry_referer": "/",
            "list": "/catalog", "list_method": "GET",
            "term_field": "term", "columns": {"course_id": 0},
            "table_marker": 'id="course-catalog"',
        },
        "auth": {"cookie": ""},
        "xnxq": "2026-2027-1",
        "courses": [{"course_id": "C1", "class_id": "A"}],
    }


def run(cfg, factory=None, cookie="sid=example"):
    return asyncio.run(prepare_native_api(
        cfg,
        environment={"KINGO_COOKIE": cookie},
        **({"factory": factory} if factory else {}),
    ))


class NativePreparationTests(TestCase):
    def client(self, rows=None):
        client = Mock()
        client.identity = AsyncMock(return_value="authenticated user")
        client.fetch_course_list = AsyncMock(return_value=rows or [])
        client.aclose = AsyncMock()
        return client

    def test_unverified_never_creates_http_client(self):
        cfg = config()
        cfg["protocol"]["verified"] = False
        factory = Mock()
        self.assertEqual(run(cfg, factory).code, "protocol_unverified")
        factory.assert_not_called()

    def test_missing_required_endpoint_never_calls_factory(self):
        cfg = config()
        cfg["protocol"]["identity_path"] = ""
        factory = Mock()
        self.assertEqual(run(cfg, factory).code, "protocol_incomplete")
        factory.assert_not_called()

    def test_browser_only_session_does_not_count(self):
        factory = Mock()
        self.assertEqual(run(config(), factory, cookie="").code, "missing_session")
        factory.assert_not_called()

    def test_missing_exact_target_never_queries_school(self):
        cfg = config()
        cfg["courses"][0]["course_id"] = "FILL_FROM_OFFICIAL_PAGE"
        factory = Mock()
        self.assertEqual(run(cfg, factory).code, "invalid_plan")
        factory.assert_not_called()

    def test_identity_then_catalog_read_in_app_and_close(self):
        client = self.client([
            CourseItem("C1", "课一", "老师", 3, "A"),
            CourseItem("C1", "课一", "老师", 0, "B"),
        ])
        result = run(config(), lambda c: client)
        self.assertEqual(result.code, "ready")
        self.assertEqual((result.catalog_rows, result.target_matches, result.known_remaining), (2, 1, 1))
        client.identity.assert_awaited_once()
        client.fetch_course_list.assert_awaited_once_with("2026-2027-1", ["C1"])
        client.aclose.assert_awaited_once()
        self.assertNotIn("老师", result.message)

    def test_identity_expired_does_not_read_catalog(self):
        client = self.client()
        client.identity.side_effect = AuthenticationError("sensitive identity")
        self.assertEqual(run(config(), lambda _: client).code, "expired")
        client.fetch_course_list.assert_not_awaited()
        client.aclose.assert_awaited_once()

    def test_rate_limited_stops_without_retrying(self):
        client = self.client()
        client.fetch_course_list.side_effect = RateLimitError("sensitive URL")
        result = run(config(), lambda _: client)
        self.assertEqual(result.code, "restricted")
        client.fetch_course_list.assert_awaited_once()
        client.aclose.assert_awaited_once()
        self.assertNotIn("URL", result.message)

    def test_catalog_failure_not_reported_ready(self):
        client = self.client()
        client.fetch_course_list.side_effect = RuntimeError("secret response")
        result = run(config(), lambda _: client)
        self.assertEqual(result.code, "unavailable")
        self.assertNotIn("secret", result.message)
        client.aclose.assert_awaited_once()


if __name__ == "__main__":
    import unittest
    unittest.main()
