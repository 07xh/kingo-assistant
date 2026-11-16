"""Synthetic NCWU 5327042 course-plan contract: never contact school."""
import asyncio
import json
from unittest import TestCase
from urllib.parse import parse_qs

import httpx

from core.ncwu_course_plan import (
    PAGE, TABLE, _scoped_fields_from_page,
    parse_course_plan, read_current_course_plan,
)

CONF = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=TEST_OWN_SESSION_ONLY"


def round_json(*, xq="0"):
    return json.dumps({
        "status": "400",
        "message": "当前时间不是有效的选课时间区段！",
        "result": json.dumps({
            "xh": "SYNTHETIC_CURRENT_USER",
            "xn": "2026", "xqM": xq, "lx": "2",
            "nj": "2026", "zydm": "0502", "yxbdm": "05",
        }),
    }, ensure_ascii=False).encode("utf-8")


def plan_page(*, pycc="05", term="0"):
    return (
        "<html><head><meta charset='GBK'></head><body>"
        "<form name='ActionForm' method='post'>"
        "<input name='xh' value='SYNTHETIC_CURRENT_USER'>"
        "<input name='xn' value='2026'>"
        f"<input name='xq' value='{term}'>"
        "<input name='nj' value='2026'>"
        "<input name='zydm' value='0502'>"
        "<input name='dwh' value='05'>"
        f"<select name='pycc'><option selected value='{pycc}'>本科</option></select>"
        "</form></body></html>"
    ).encode("gbk")


def table(*, remaining="6", total="2"):
    return (
        "<html><script>parent.showTotalRecord('5327042','" + total + "')</script>"
        "<table><tr class='O' id='tr1'>"
        "<td id='tr1_kc'>[00001234]工程制图</td>"
        "<td id='tr1_curent_skbjdm'>00001234-01</td>"
        "<td id='tr1_skbjmc'>普通教学班</td>"
        "<td id='tr1_xkrssx'>135</td>"
        "<td id='tr1_xkrs'>136/0</td>"
        f"<td id='tr1_qdrs'>{remaining}</td>"
        "<td id='tr1_rkjs'>测试教师</td>"
        "<td id='tr1_sksj'>周一</td></tr>"
        "<tr class='E' id='tr2'>"
        "<td id='tr2_sksj'>周三</td>"
        "<td id='tr2_skdd'>实验楼</td></tr>"
        "</table></html>"
    ).encode("gbk")


def run(handler, *, config=None, cookie=COOKIE):
    seen = []
    def capture(req):
        seen.append(req)
        return handler(req)
    result = asyncio.run(read_current_course_plan(
        config or CONF, session_cookie=cookie,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(capture),
            follow_redirects=False),
        enforce_manual_cooldown=False,
    ))
    return result, seen


class PlanReadTests(TestCase):
    def valid_response(self, req):
        self.assertEqual(req.url.host, "jwmis.ncwu.edu.cn")
        self.assertIn("TEST_OWN_SESSION_ONLY", req.headers["cookie"])
        if req.url.path.endswith("/getWsxkTimeRange.action"):
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.url.query.decode(), "xktype=2")
            return httpx.Response(200, content=round_json())
        if req.url.path == "/hsjw/student/wsxk.kcbcx10319.html":
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.url.query.decode(), "menucode=S2020103")
            return httpx.Response(200, content=plan_page(),
                                  headers={"content-type": "text/html; charset=GBK"})
        if req.url.path == "/hsjw/taglib/DataTable.jsp":
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.url.query.decode(), "tableId=5327042&clientWidth=1210")
            form = parse_qs(req.content.decode("ascii"), encoding="gbk")
            self.assertEqual(form["xn"], ["2026"])
            self.assertEqual(form["xq"], ["0"])
            self.assertEqual(form["zydm"], ["0502"])
            self.assertEqual(form["btnFilter"], ["类别过滤"])
            self.assertEqual(form["btnSubmit"], ["提交"])
            self.assertIn("charset=GBK", req.headers["content-type"])
            return httpx.Response(200, content=table(),
                                  headers={"content-type": "text/html; charset=GBK"})
        self.fail("unexpected path")

    def test_three_reads_retrieve_plan_when_selection_window_closed(self):
        result, seen = run(self.valid_response)
        self.assertEqual([r.method for r in seen], ["POST", "GET", "POST"])
        self.assertEqual(result.code, "read")
        self.assertEqual(result.academic_year, "2026")
        self.assertEqual(result.academic_term, "0")
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(result.record_count, 2)
        row = result.rows[0]
        self.assertEqual(row.name, "工程制图")
        self.assertEqual(row.code, "00001234")
        self.assertEqual(row.class_id, "00001234-01")
        self.assertEqual(row.plan_available, 6)
        self.assertEqual(row.capacity, 135)
        self.assertEqual(row.enrolled, 136)
        self.assertIn("周三", row.time)
        self.assertIn("快照", result.message)
        self.assertNotIn("SYNTHETIC_CURRENT_USER", str(result))

    def test_qdrs_not_computed_from_capacity_minus_enrolled(self):
        for raw in ("", "-1", "NaN", "可选6", "1/2", "１００"):
            with self.subTest(raw=raw):
                rows, total = parse_course_plan(table(remaining=raw).decode("gbk"))
                self.assertIsNone(rows[0].plan_available)
                self.assertEqual(rows[0].capacity, 135)
                self.assertEqual(rows[0].enrolled, 136)

    def test_empty_catalog_only_when_total_explicitly_zero(self):
        items, total = parse_course_plan(
            "<html><script>showTotalRecord('5327042','0')</script></html>")
        self.assertEqual(total, 0)
        self.assertEqual(items, ())

    def test_malformed_table_cannot_claim_empty_catalog(self):
        with self.assertRaises(ValueError):
            parse_course_plan("<html><table></table></html>")
        with self.assertRaises(ValueError):
            parse_course_plan(
                "<html><script>showTotalRecord('5327042','12')</script></html>")

    def test_missing_pycc_blocks_before_catalog_post(self):
        def handler(req):
            if req.url.path.endswith("getWsxkTimeRange.action"):
                return httpx.Response(200, content=round_json())
            return httpx.Response(
                200, content=plan_page(pycc=""),
                headers={"content-type": "text/html; charset=GBK"})
        result, seen = run(handler)
        self.assertEqual(result.code, "form_unavailable")
        self.assertEqual(len(seen), 2)

    def test_mismatching_semester_blocks_before_catalog_post(self):
        def handler(req):
            if req.url.path.endswith("getWsxkTimeRange.action"):
                return httpx.Response(200, content=round_json(xq="1"))
            return httpx.Response(200, content=plan_page(term="0"),
                                  headers={"content-type": "text/html; charset=GBK"})
        result, seen = run(handler)
        self.assertEqual(result.code, "form_unavailable")
        self.assertEqual(len(seen), 2)

    def test_redirect_stops_without_following(self):
        result, seen = run(lambda req: httpx.Response(
            302, headers={"location": "https://authserver.ncwu.edu.cn/"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(seen), 1)

    def test_restriction_429_stops_without_retry(self):
        result, seen = run(lambda req: httpx.Response(429))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(seen), 1)

    def test_missing_own_session_makes_zero_requests(self):
        result, seen = run(lambda req: self.fail("no network"), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(seen, [])

    def test_cross_origin_makes_zero_requests(self):
        result, seen = run(lambda req: self.fail("no network"),
                           config={"base_url": "https://jwmis.ncwu.edu.cn.other.example"})
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(seen, [])

    def test_login_expired_html_200_prevents_table_post(self):
        def handler(req):
            if req.url.path.endswith("getWsxkTimeRange.action"):
                return httpx.Response(200, content=round_json())
            return httpx.Response(200,
                content="凭证已失效".encode("gbk"),
                headers={"content-type": "text/html; charset=GBK"})
        result, seen = run(handler)
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
