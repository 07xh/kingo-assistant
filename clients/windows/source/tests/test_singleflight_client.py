"""Synthetic request coalescing; never contacts a university server."""
import asyncio
import unittest

import httpx

from core.client import KingoClient
from tests.test_local import config, html


class CoalescedCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_identical_catalog_queries_share_one_request(self):
        paths = []
        async def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/page":
                await asyncio.sleep(0.01)
                return httpx.Response(200, content=b"<html>ok</html>")
            if request.url.path == "/catalog":
                await asyncio.sleep(0.02)
                return httpx.Response(200, content=html(),
                                      headers={"content-type": "text/html; charset=gbk"})
            raise AssertionError("unexpected synthetic request")
        client = KingoClient(config(), offline_test_mode=True)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                        follow_redirects=False)
        try:
            first, second = await asyncio.gather(
                client.fetch_course_list("2026-2027-1", ["TEST101"]),
                client.fetch_course_list("2026-2027-1", ["TEST101"]),
            )
            self.assertEqual([row.course_id for row in first], ["TEST101"])
            self.assertEqual([row.course_id for row in second], ["TEST101"])
            self.assertIsNot(first, second)
            self.assertEqual(paths.count("/page"), 1)
            self.assertEqual(paths.count("/catalog"), 1)
            # Reuse is in-flight only; later checks obtain fresh state.
            await client.fetch_course_list("2026-2027-1", ["TEST101"])
            self.assertEqual(paths.count("/catalog"), 2)
        finally:
            await client.aclose()


if __name__ == "__main__":
    unittest.main()
