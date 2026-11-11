"""Contract wiring regression against httpx.MockTransport, never a school host."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from core.client import KingoClient, RateLimitError, ProtocolError
from core.request_budget import PolicyError, read_verified_policy_config
from core.time_sync import TimeSync
from tests.test_local import config as synthetic_config


class BudgetedClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "quota.sqlite"
        self.cfg = synthetic_config()
        self.cfg["server_policy"] = {
            "verified": True,
            "source": "synthetic local unit-test contract",
            "origin": "https://campus.example.edu",
            "windows": [[0.1, 2], [1.0, 20]],
            "max_concurrency": 1,
            "database": str(self.path),
        }

    async def client(self, handler):
        client = KingoClient(self.cfg)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                       follow_redirects=False)
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_missing_policy_blocks_before_network_client_creation(self):
        bad = synthetic_config()
        with self.assertRaises(PolicyError):
            KingoClient(bad)
        with self.assertRaises(PolicyError):
            read_verified_policy_config(self.cfg | {
                "server_policy": self.cfg["server_policy"] | {
                    "origin": "https://other.example.edu"
                }
            }, ("https", "campus.example.edu"))

    async def test_429_cooldown_persists_across_new_client(self):
        calls = []
        async def handler(req):
            calls.append(req.url.path)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return httpx.Response(200, content=b"ok")
        first = await self.client(handler)
        with self.assertRaises(RateLimitError):
            await first.read("GET", "/mine", "/")
        with closing(sqlite3.connect(self.path)) as conn:
            cooldown_row = conn.execute(
                "SELECT value FROM metadata WHERE key='backoff_until'"
            ).fetchone()
        self.assertIsNotNone(cooldown_row)
        second = await self.client(handler)
        response = await second.read("GET", "/mine", "/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)
        with closing(sqlite3.connect(self.path)) as conn:
            stamps = [row[0] for row in conn.execute(
                "SELECT at FROM requests ORDER BY at"
            )]
        # The sliding-log table can prune the first (429) reservation.
        # The next request must not be reserved before the persisted deadline.
        self.assertTrue(stamps)
        self.assertGreaterEqual(stamps[-1] + 0.002, cooldown_row[0])

    async def test_all_requests_take_quota_and_no_second_send_without_capacity(self):
        count = 0
        def handler(req):
            nonlocal count
            count += 1
            return httpx.Response(200)
        client = await self.client(handler)
        await client.read("GET", "/mine", "/")
        await client.read("GET", "/mine", "/")
        await client.read("GET", "/mine", "/")
        self.assertEqual(count, 3)
        with closing(sqlite3.connect(self.path)) as conn:
            stamps = [row[0] for row in conn.execute(
                "SELECT at FROM requests ORDER BY at"
            )]
        self.assertEqual(len(stamps), 3)
        # Assert the actual 100ms sliding-window admission invariant, not
        # just the final call's wall time (prior reads may have spent time).
        self.assertGreaterEqual(stamps[2] - stamps[0], 0.097)

    async def test_no_offline_escape_without_mock_transport(self):
        fake = KingoClient(synthetic_config(), offline_test_mode=True)
        self.addAsyncCleanup(fake.aclose)
        with self.assertRaisesRegex(ProtocolError, "MockTransport"):
            await fake.read("GET", "/mine", "/")

    async def test_http_date_fallback_fails_closed_without_budgeted_reader(self):
        clock = TimeSync()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, headers={"Date": "Wed, 11 Nov 2026 08:00:00 GMT"}))) as http:
            with patch.object(TimeSync, "_ntp", side_effect=OSError("offline test")):
                with self.assertRaisesRegex(ValueError, "统一预算"):
                    await clock.sync(http, "https://campus.example.edu/mine",
                                     "https://campus.example.edu/")

    async def test_http_date_fallback_uses_budgeted_client(self):
        calls = []
        def handler(req):
            calls.append(req.url.path)
            return httpx.Response(200, headers={
                "Date": format_datetime(datetime.now(timezone.utc) + timedelta(seconds=1)),
                "Age": "0",
            })
        client = await self.client(handler)
        with patch.object(TimeSync, "_ntp", side_effect=OSError("offline test")):
            await TimeSync().sync(
                client.http, "https://campus.example.edu/mine",
                "https://campus.example.edu/",
                budgeted_get=lambda url, ref: client.read("GET", url, ref),
            )
        self.assertEqual(calls, ["/mine"])
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM requests").fetchone()[0], 1)

    def test_retry_after_header_http_date_or_invalid(self):
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=12))
        value = KingoClient._retry_after_seconds(date)
        self.assertIsNotNone(value)
        self.assertGreater(value, 9.0)
        self.assertIsNone(KingoClient._retry_after_seconds("wrong"))
        self.assertEqual(KingoClient._retry_after_seconds("0"), 0.0)


if __name__ == "__main__":
    unittest.main()
