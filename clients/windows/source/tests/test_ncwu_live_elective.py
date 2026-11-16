"""Mocked first-party HTTP only: test fresh form capture, no writes or credentials."""
import asyncio
import json
from unittest import TestCase
from unittest.mock import patch

import httpx

from core.ncwu_live_elective import (
    CATALOG_PATH, HOST, REFERER, S202_PAGE, WINDOW_PATH,
    _extract_query_form, live_s202_snapshot,
)
from core.ncwu_elective_table import EXPECTED_HEADINGS


COOKIE = "JSESSIONID=SYNTHETIC_SESSION_12345"
CONF = {"base_url": "https://jwmis.ncwu.edu.cn"}


def page(*, mode="2"):
    return (
        "<html><meta charset='GBK'>"
        "<form id='ActionForm' method='post'>"
        f"<input name='xktype' value='{mode}'>"
        "<input name='xn' value='2026'><input name='xq' value='0'>"
        "<input name='lcid' value='CURRENT_SESSION_ROUND'>"
        "<input name='xh' value='SESSION_SCOPED_VALUE'>"
        "</form></html>"
    ).encode("gbk")


def dynamic_page():
    """S202 first-party HTML has empty fields before its window AJAX runs."""
    return (
        "<html><meta charset='GBK'>"
        "<form id='ActionForm' method='post'>"
        "<input name='xktype' value='2'>"
        "<input name='xh'><input name='xn'><input name='xq'>"
        "<input name='lcid'><input name='nj'><input name='zydm'>"
        "<input name='_kcfw'><input name='initQry' value='1'>"
        "</form></html>"
    ).encode("gbk")


def catalog(*, row=True):
    headers = "".join(f"<th id='{name}'>{name}</th>" for name in EXPECTED_HEADINGS)
    fields = [""] * 17
    fields[0] = "仅供测试课程"
    fields[5] = "SYNTHETIC_CLASS"
    fields[8] = "测试教师"
    fields[9] = "10"
    fields[12] = "需核实"
    fields[14] = "SYNTHETIC_COURSE"
    cells = "".join(f"<td>{value}</td>" for value in fields)
    rows = f"<tr>{cells}</tr>" if row else ""
    return (
        "<html><meta charset='GBK'><table><tr>" + headers
        + "</tr>" + rows + "</table></html>"
    ).encode("gbk")


def run(handler, config=None, cookie=COOKIE):
    seen = []
    def handle(request):
        seen.append(request)
        return handler(request)
    result = asyncio.run(live_s202_snapshot(
        config or CONF, session_cookie=cookie,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            follow_redirects=False,
        ),
        enforce_manual_cooldown=False,
    ))
    return result, seen


class LiveS202Tests(TestCase):
    def handler(self, request):
        self.assertEqual(request.url.host, HOST)
        self.assertEqual(request.headers["referer"], REFERER)
        self.assertIn("SYNTHETIC_SESSION", request.headers["cookie"])
        if request.url.path == S202_PAGE:
            self.assertEqual(request.method, "GET")
            return httpx.Response(200, content=page(),
                headers={"content-type": "text/html; charset=GBK"})
        if request.url.path == WINDOW_PATH:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.content, b"")
            return httpx.Response(200, json={
                "status": "400", "message": "当前时间不是有效的选课时间区段！",
            })
        if request.url.path == "/hsjw/taglib/DataTable.jsp":
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.query.decode(), "tableId=2568&fre=1")
            form = request.content.decode()
            self.assertIn("lcid=CURRENT_SESSION_ROUND", form)
            self.assertIn("xh=SESSION_SCOPED_VALUE", form)
            return httpx.Response(200, content=catalog(),
                headers={"content-type": "text/html; charset=GBK"})
        self.fail("Unexpected request " + request.url.path)

    def test_real_read_flow_with_closed_window_still_fetches_catalog(self):
        result, requests = run(self.handler)
        self.assertEqual([r.method for r in requests], ["GET", "POST", "POST"])
        self.assertEqual(result.code, "catalog_read")
        self.assertEqual(result.window, "closed")
        self.assertEqual(result.recognized_headers, 17)
        self.assertEqual(len(result.courses), 1)
        self.assertEqual(result.courses[0].course_code, "SYNTHETIC_COURSE")
        self.assertFalse(hasattr(result.courses[0], "remain"))
        self.assertNotIn("SESSION_SCOPED_VALUE", result.message)

    def test_dynamic_first_party_page_hydrates_after_open_window(self):
        def handler(req):
            if req.url.path == S202_PAGE:
                return httpx.Response(200, content=dynamic_page(), headers={
                    "content-type": "text/html; charset=GBK"})
            if req.url.path == WINDOW_PATH:
                self.assertEqual(req.url.query.decode(), "xktype=2")
                return httpx.Response(200, json={
                    "status": "200", "result": json.dumps({
                        "lx": "2", "xh": "TEST_SELF_SCOPED",
                        "xn": "2026", "xqM": "0",
                        "lcid": "FRESH_SYNTHETIC_ROUND",
                        "nj": "2026", "zydm": "TEST_MAJOR",
                        "kcfw": "zxbnj",
                    }),
                })
            self.assertEqual(req.url.path, "/hsjw/taglib/DataTable.jsp")
            posted = req.content.decode()
            for required in ("xh=TEST_SELF_SCOPED", "xn=2026",
                             "xq=0", "lcid=FRESH_SYNTHETIC_ROUND",
                             "_kcfw=zxbnj"):
                self.assertIn(required, posted)
            return httpx.Response(200, content=catalog(row=True), headers={
                "content-type": "text/html; charset=GBK"})
        result, requests = run(handler)
        self.assertEqual(result.code, "catalog_read")
        self.assertEqual(result.window, "reported_open")
        self.assertEqual(len(requests), 3)

    def test_closed_window_does_not_hydrate_empty_first_party_page(self):
        def handler(req):
            if req.url.path == S202_PAGE:
                return httpx.Response(200, content=dynamic_page(), headers={
                    "content-type": "text/html; charset=GBK"})
            self.assertEqual(req.url.path, WINDOW_PATH)
            return httpx.Response(200, json={
                "status": "400",
                "message": "当前时间不是有效的选课时间区段！",
                "result": json.dumps({
                    "xh": "ARCHIVED", "xn": "2026",
                    "xqM": "0", "lcid": "STALE",
                }),
            })
        result, requests = run(handler)
        self.assertEqual(result.code, "window_closed")
        self.assertEqual(result.window, "closed")
        self.assertEqual(len(requests), 2)

    def test_dynamic_page_missing_current_round_fails_closed(self):
        def handler(req):
            if req.url.path == S202_PAGE:
                return httpx.Response(200, content=dynamic_page(), headers={
                    "content-type": "text/html; charset=GBK"})
            return httpx.Response(200, json={
                "status": "200", "result": "{\\"xn\\":\\"2026\\"}",
            })
        result, requests = run(handler)
        self.assertEqual(result.code, "form_incomplete")
        self.assertEqual(len(requests), 2)

    def test_missing_session_makes_zero_network_calls(self):
        result, requests = run(
            lambda req: self.fail("network not expected"), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(requests, [])

    def test_impostor_host_makes_zero_network_calls(self):
        result, requests = run(
            lambda req: self.fail("network not expected"),
            config={"base_url": "https://jwmis.ncwu.edu.cn.evil.example"})
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(requests, [])

    def test_page_redirect_stops_before_catalog_post(self):
        result, requests = run(lambda req: httpx.Response(
            302, headers={"location": "https://authserver.ncwu.edu.cn/"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_empty_window_still_calls_readonly_catalog(self):
        def handler(req):
            if req.url.path == S202_PAGE:
                return httpx.Response(200, content=page(), headers={
                    "content-type": "text/html; charset=GBK"})
            if req.url.path == WINDOW_PATH:
                return httpx.Response(200, json={"status": "400", "message": "当前时间不是有效的选课时间区段！"})
            return httpx.Response(200, content=catalog(row=False),
                headers={"content-type": "text/html; charset=GBK"})
        result, requests = run(handler)
        self.assertEqual(result.code, "catalog_read")
        self.assertEqual(result.window, "closed")
        self.assertEqual(result.courses, ())
        self.assertEqual(len(requests), 3)

    def test_unrecognized_form_cannot_be_rebuilt_from_old_tokens(self):
        def handler(req):
            return httpx.Response(200, content=page(mode="1"),
                headers={"content-type": "text/html; charset=GBK"})
        result, requests = run(handler)
        self.assertEqual(result.code, "form_incomplete")
        self.assertEqual(len(requests), 1)

    def test_expired_html_200_does_not_count_as_a_catalog(self):
        result, requests = run(lambda req: httpx.Response(
            200, content="凭证已失效".encode("gbk"),
            headers={"content-type": "text/html; charset=GBK"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_school_429_stops_without_retry(self):
        result, requests = run(lambda req: httpx.Response(429))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(requests), 1)

    def test_ambiguous_form_is_rejected(self):
        raw = page().decode("gbk").replace(
            "<input name='xn' value='2026'>",
            "<input name='xn' value='2026'><input name='xn' value='2025'>")
        with self.assertRaises(ValueError):
            _extract_query_form(raw)
