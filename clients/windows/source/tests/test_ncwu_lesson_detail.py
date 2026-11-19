"""Bounded, mocked contract tests for the independently observed schedule AJAX."""
import asyncio
from unittest import TestCase

import httpx

from core.ncwu_lesson_detail import (
    PATH,
    LessonCell,
    parse_lesson_detail,
    read_lesson_detail,
)

CONFIG = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=SYNTHETIC_OWNER_ONLY"
TIMES = (
    ("08:00", "08:45"), ("08:55", "09:40"), ("09:55", "10:40"),
    ("10:50", "11:35"), ("11:45", "12:30"), ("14:00", "14:45"),
    ("14:55", "15:40"), ("15:55", "16:40"), ("16:50", "17:35"),
    ("17:45", "18:30"), ("19:00", "19:45"), ("19:55", "20:40"),
)


def school_html():
    heading = ("<tr><th>时段</th><th>节次</th>" +
               "".join(f"<th>星期{day}</th>" for day in "一二三四五六日") +
               "</tr>")
    rows = []
    for index, (begin, end) in enumerate(TIMES, 1):
        # Real HTML can put a merged time-of-day cell ahead of section 1.
        prefix = "<td>上午</td>" if index == 1 else ""
        days = "".join(
            "<td>工程制图 测试教师(7-14 [江]测试楼101)</td>"
            if index == 1 and day == 1 else "<td></td>"
            for day in range(1, 8)
        )
        rows.append(f"<tr>{prefix}<td>{index}({begin}-{end})</td>{days}</tr>")
    return ("<html><table><tr><th colspan='9'>"
            "2026-2027学年第一学期</th></tr>" + heading +
            "".join(rows) + "</table></html>")


def call(handler, *, semester="2026-2027-1", cookie=COOKIE, config=None):
    requests = []

    def handle(request):
        requests.append(request)
        return handler(request)

    result = asyncio.run(read_lesson_detail(
        CONFIG if config is None else config,
        session_cookie=cookie, semester=semester,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handle), follow_redirects=False),
        enforce_manual_cooldown=False,
    ))
    return result, requests


class NcwuLessonDetailTests(TestCase):
    def test_parses_only_unverified_cells_and_official_times(self):
        result = parse_lesson_detail(school_html(), "2026-2027-1")
        self.assertEqual(result.code, "read")
        self.assertEqual(len(result.section_times), 12)
        self.assertEqual(result.section_times[10], (11, "19:00", "19:45"))
        self.assertEqual(result.cells, (
            LessonCell(1, 1, "工程制图 测试教师(7-14 [江]测试楼101)"),))
        self.assertFalse(result.verified)
        self.assertFalse(result.trusted_sync_allowed)

    def test_owner_session_one_post_and_explicit_term_conversion(self):
        def handler(req):
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.url.host, "jwmis.ncwu.edu.cn")
            self.assertEqual(req.url.path, PATH)
            self.assertEqual(req.headers["referer"],
                             "https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action")
            self.assertIn("SYNTHETIC_OWNER_ONLY", req.headers["cookie"])
            self.assertEqual(req.content, b"weeks=&xnxq=2026%2C0")
            return httpx.Response(200, content=school_html().encode("utf-8"),
                                  headers={"content-type": "text/html; charset=UTF-8"})
        result, requests = call(handler)
        self.assertEqual(result.code, "read")
        self.assertEqual(len(requests), 1)

    def test_wrong_semester_does_not_promote_html(self):
        self.assertEqual(
            parse_lesson_detail(school_html(), "2026-2027-2").code,
            "schema_unverified")

    def test_grid_missing_day_or_section_fails_closed(self):
        self.assertEqual(parse_lesson_detail(
            school_html().replace("星期三", "星期四"),
            "2026-2027-1").code, "schema_unverified")
        self.assertEqual(parse_lesson_detail(
            school_html().replace("12(19:55-20:40)", "13(19:55-20:40)"),
            "2026-2027-1").code, "schema_unverified")

    def test_200_expiry_page_is_not_success(self):
        result, requests = call(lambda _: httpx.Response(
            200, content="凭证已失效".encode(),
            headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_redirect_and_rate_limit_do_not_retry(self):
        for status, code in ((302, "expired"), (429, "restricted")):
            result, requests = call(lambda _: httpx.Response(status))
            self.assertEqual(result.code, code)
            self.assertEqual(len(requests), 1)

    def test_invalid_host_and_missing_session_produce_no_http(self):
        no_net = lambda req: self.fail("network not expected")
        result, requests = call(no_net, cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(requests, [])
        result, requests = call(no_net, config={
            "base_url": "https://jwmis.ncwu.edu.cn.evil.example"})
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(requests, [])

    def test_old_wap_json_is_not_accepted_as_new_html(self):
        result, _ = call(lambda _: httpx.Response(
            200, content=b'{"week1":[]}', headers={"content-type": "application/json"}))
        self.assertEqual(result.code, "schema_unverified")


if __name__ == "__main__":
    import unittest
    unittest.main()
