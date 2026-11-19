"""NCWU read-only transport refactor has stable request and failure semantics."""
import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, Mock

import httpx

from core.ncwu_read_support import (
    classify_read_response,
    default_read_client,
    get_optional_verified_budget,
    set_scoped_cookie,
)


def response(status=200, *, headers=None, content=b"OK"):
    return httpx.Response(
        status, headers=headers or {}, content=content,
        request=httpx.Request("GET", "https://jwmis.ncwu.edu.cn/hsjw/test"),
    )


class NCWUReadSupportTests(TestCase):
    def test_default_client_is_bounded_and_does_not_follow_redirects(self):
        client = default_read_client()
        try:
            self.assertFalse(client.follow_redirects)
            self.assertEqual(client.timeout.connect, 5.0)
            self.assertEqual(client.timeout.read, 10.0)
            self.assertEqual(client.timeout.write, 10.0)
        finally:
            asyncio.run(client.aclose())

    def test_cookie_is_only_sent_to_school_hsjw_path(self):
        async def examine():
            async with httpx.AsyncClient() as client:
                set_scoped_cookie(client, "JSESSIONID=TEST_SECRET; OTHER=LOCAL")
                school = client.cookies.jar
                items = list(school)
                self.assertEqual({x.name for x in items}, {"JSESSIONID", "OTHER"})
                self.assertTrue(all(x.domain == "jwmis.ncwu.edu.cn" for x in items))
                self.assertTrue(all(x.path == "/hsjw" for x in items))
                self.assertTrue(all(x.secure is False for x in items))
        asyncio.run(examine())

    def test_budget_factory_runs_only_with_verified_policy(self):
        factory = Mock(return_value=object())
        self.assertIsNone(get_optional_verified_budget(
            {"base_url": "https://jwmis.ncwu.edu.cn"}, factory))
        factory.assert_not_called()
        cfg = {"server_policy": {"verified": True}}
        value = get_optional_verified_budget(cfg, factory)
        self.assertIsNotNone(value)
        factory.assert_called_once_with(cfg, ("https", "jwmis.ncwu.edu.cn"))

    def test_rejected_responses_do_not_ever_retry(self):
        for status, expected in (
            (302, "expired"), (401, "expired"), (403, "restricted"),
            (429, "restricted"), (500, "unavailable"),
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    asyncio.run(classify_read_response(response(status))),
                    expected,
                )

    def test_429_imposes_retry_after_on_existing_budget(self):
        budget = Mock()
        budget.impose_retry_after = AsyncMock()
        result = asyncio.run(classify_read_response(
            response(429, headers={"Retry-After": "12"}), budget,
        ))
        self.assertEqual(result, "restricted")
        budget.impose_retry_after.assert_awaited_once_with(12.0)

    def test_429_unparseable_retry_after_uses_60_seconds(self):
        budget = Mock()
        budget.impose_retry_after = AsyncMock()
        result = asyncio.run(classify_read_response(
            response(429, headers={"Retry-After": "invalid"}), budget,
        ))
        self.assertEqual(result, "restricted")
        budget.impose_retry_after.assert_awaited_once_with(60.0)

    def test_empty_bodies_and_size_limits_are_per_endpoint(self):
        self.assertIsNone(asyncio.run(classify_read_response(
            response(200, content=b""))))
        self.assertEqual(asyncio.run(classify_read_response(
            response(200, content=b""), require_content=True)), "unavailable")
        self.assertEqual(asyncio.run(classify_read_response(
            response(200, content=b"ABCD"), max_bytes=3)), "unavailable")

    def test_score_expiry_scan_is_strictly_bounded(self):
        keywords = ("凭证已失效",)
        self.assertEqual(asyncio.run(classify_read_response(
            response(200, content="凭证已失效".encode()),
            expired_markers=keywords, expired_scan_max_bytes=16000,
        )), "expired")
        self.assertIsNone(asyncio.run(classify_read_response(
            response(200, content=("凭证已失效" + "A" * 16001).encode("utf-8")),
            expired_markers=keywords, expired_scan_max_bytes=16000,
        )))


if __name__ == "__main__":
    import unittest
    unittest.main()
