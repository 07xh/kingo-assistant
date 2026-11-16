"""Volatile native-login cookie is used by subsequent read-only operations."""
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, Mock

from core.client import CourseItem
from core.native_preparation import prepare_native_api
from core.session_probe import probe_own_session


def config():
    return {
        "protocol": {
            "verified": True, "identity_path": "/who",
            "identity_referer": "/", "identity_selector": "#name",
            "entry": "/select", "entry_referer": "/", "list": "/catalog",
            "list_method": "GET", "term_field": "term",
            "columns": {"course_id": 0}, "table_marker": "courses",
        },
        "auth": {"cookie": ""},
        "xnxq": "2026-2027-1",
        "courses": [{"course_id": "C1", "class_id": "A"}],
    }


class NativeSessionIntegrationTest(TestCase):
    def test_native_cookie_reaches_catalog_without_legacy_env(self):
        client = Mock()
        client.identity = AsyncMock(return_value="authorized student")
        client.fetch_course_list = AsyncMock(return_value=[
            CourseItem("C1", "example", "teacher", 2, "A")
        ])
        client.aclose = AsyncMock()
        received = []
        def factory(cfg, *, session_cookie):
            received.append(session_cookie)
            return client
        report = asyncio.run(prepare_native_api(
            config(), environment={}, session_cookie="SID=volatile",
            factory=factory,
        ))
        self.assertEqual(report.code, "ready")
        self.assertEqual(received, ["SID=volatile"])
        client.fetch_course_list.assert_awaited_once()
        self.assertNotIn("SID=volatile", report.message)

    def test_native_cookie_reaches_identity_probe(self):
        client = Mock()
        client.identity = AsyncMock(return_value="authorized student")
        client.aclose = AsyncMock()
        received = []
        def factory(cfg, *, session_cookie):
            received.append(session_cookie)
            return client
        report = asyncio.run(probe_own_session(
            config(), environment={}, session_cookie="SID=volatile",
            factory=factory,
        ))
        self.assertTrue(report.verified)
        self.assertEqual(received, ["SID=volatile"])
        client.aclose.assert_awaited_once()

    def test_explicitly_cleared_cookie_does_not_fallback(self):
        client_factory = Mock()
        result = asyncio.run(probe_own_session(
            config(), environment={"KINGO_COOKIE": "SID=old"},
            session_cookie="", factory=client_factory,
        ))
        self.assertEqual(result.code, "missing_session")
        client_factory.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
