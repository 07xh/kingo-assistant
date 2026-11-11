"""Offline behavior tests; no network connection to any university site."""
import asyncio
import time
import unittest
from collections import Counter

import httpx

from core.client import (CourseItem, KingoClient, AuthenticationError, RateLimitError,
                         encode_gbk_form, parse_courses)
from core.scheduler import Scheduler


def config():
    return {
        "base_url": "https://campus.example.edu", "xnxq": "2026-2027-1",
        "auth": {"cookie": "TEST=local"},
        "strategy": {"mode": "monitor", "interval_range": [60, 90], "max_cycles": 2, "dry_run": True},
        "courses": [
            {"key": "a", "course_id": "TEST101", "course_name": "", "teacher": ""},
            {"key": "b", "course_id": "TEST202", "course_name": "", "teacher": ""}],
        "notify": {},
        "protocol": {"verified": True,
                     "identity_path": "/mine", "identity_referer": "/",
                     "identity_selector": "span#person", "entry": "/page",
                     "entry_referer": "/", "list": "/catalog",
                     "list_method": "POST", "list_fields": {"kind": "选修课"},
                     "term_field": "term", "columns": {"course_id": 0, "course_name": 1,
                                                           "teacher": 2, "remain": 3}}
    }


def html():
    return ("<html><form><table><tr><th>代码</th><th>课程</th></tr>"
            "<tr><td>TEST101</td><td>电路实验</td><td>张老师</td><td>1</td></tr>"
            "<tr><td>TEST202</td><td>高数</td><td>李老师</td><td>0</td></tr>"
            "<tr><td>TEST303</td><td>物理</td><td>王老师</td><td>未知</td></tr>"
            "</table></form></html>").encode("gbk")


class ParserTest(unittest.TestCase):
    def test_gbk_query_is_precompiled_and_correct(self):
        body = encode_gbk_form([("term", "2026-2027-1"), ("type", "选修课")])
        self.assertTrue(body.startswith(b"term="))
        self.assertIn(b"%D1%A1%D0%DE%BF%CE", body)
        self.assertNotIn("选修课".encode("utf-8"), body)

    def test_fast_row_filter_and_missing_seats(self):
        rows = parse_courses(html(), config()["protocol"], ["TEST101", "TEST303"])
        self.assertEqual([r.course_id for r in rows], ["TEST101", "TEST303"])
        self.assertEqual(rows[0].remain, 1)
        self.assertIsNone(rows[1].remain)
        self.assertEqual(len(parse_courses(html(), config()["protocol"])), 3)

    def test_bad_structure_does_not_fabricate_available_seats(self):
        raw = b"<table><tr><td>TEST101</td><td>Course</td><td>Teacher</td><td>maybe 12</td></tr></table>"
        self.assertIsNone(parse_courses(raw, config()["protocol"])[0].remain)


class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_query_cached_and_single_request(self):
        request_log = []
        async def handler(req):
            request_log.append((req.method, req.url.path, bytes(req.content)))
            if req.url.path == "/mine":
                return httpx.Response(200, content="<span id='person'>测试用户</span>".encode("gbk"))
            if req.url.path == "/page":
                return httpx.Response(200, content=b"<html>ok</html>")
            if req.url.path == "/catalog":
                self.assertEqual(req.method, "POST")
                self.assertEqual(req.content.count(b"kind="), 1)
                return httpx.Response(200, content=html(), headers={"content-type": "text/html; charset=gbk"})
            raise AssertionError("unknown request")
        client = KingoClient(config())
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
        try:
            self.assertEqual(await client.identity(), "测试用户")
            q1 = client.prepare_query("2026-2027-1")
            q2 = client.prepare_query("2026-2027-1")
            self.assertIs(q1, q2)  # same cached body bytes, not re-encoded per cycle
            rows = await client.fetch_course_list("2026-2027-1", ["TEST101", "TEST202"])
            self.assertEqual(len(rows), 2)
            self.assertEqual(request_log.count(("GET", "/page", b"")), 1)
            self.assertEqual(len([r for r in request_log if r[1] == "/catalog"]), 1)
        finally:
            await client.aclose()

    async def test_429_and_redirect_stop_without_retry(self):
        count = Counter()
        def handler(req):
            count["requests"] += 1
            return httpx.Response(429)
        client = KingoClient(config())
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(RateLimitError):
                await client.identity()
            self.assertEqual(count["requests"], 1)
        finally:
            await client.aclose()


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_targets_use_one_fetch_no_enrollment(self):
        class Dummy:
            def __init__(self):
                self.stop = asyncio.Event()
                self.calls = 0
            async def fetch_course_list(self, *args):
                self.calls += 1
                return [CourseItem("TEST101", "电路实验", "张老师", 1),
                        CourseItem("TEST202", "高数", "李老师", 0)]
        dummy = Dummy()
        scheduler = Scheduler(dummy, None, config())
        notifications = []
        async def notify(target, item):
            notifications.append(item.course_id)
        scheduler.notify = notify
        await scheduler.cycle()
        await scheduler.cycle()
        self.assertEqual(dummy.calls, 2)  # once per cycle, not once per course
        self.assertEqual(notifications, ["TEST101"])
        self.assertTrue(scheduler.states["a"].startswith("发现余量"))
        self.assertEqual(scheduler.states["b"], "已满")


if __name__ == "__main__":
    unittest.main()
