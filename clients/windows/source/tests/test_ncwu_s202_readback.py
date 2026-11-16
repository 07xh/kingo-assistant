"""Synthetic HTTP contract tests: never contact the campus server."""
import asyncio
from unittest import TestCase

import httpx

from core.ncwu_s202_readback import (
    CandidateSelectedCourse, SELECTED_PAGE, S202ReadbackResult,
    parse_s202_readback, read_s202_selected_results,
)

CONF = {"base_url": "https://jwmis.ncwu.edu.cn"}
COOKIE = "JSESSIONID=SYNTHETIC_ONLY"


def selected_html():
    return (
        "<html><meta charset='GBK'><body><h2>选课结果</h2>"
        "<table><tr><th>课程名称</th><th>课程代码</th>"
        "<th>教学班号</th><th>任课教师</th></tr>"
        "<tr><td>测试课程</td><td>TEST_COURSE</td>"
        "<td>TEST_CLASS</td><td>测试教师</td></tr></table>"
        "</body></html>"
    ).encode("gbk")


def call(handler, *, cookie=COOKIE, config=None):
    requests = []

    def transport(req):
        requests.append(req)
        return handler(req)

    result = asyncio.run(read_s202_selected_results(
        config or CONF, session_cookie=cookie,
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(transport),
            follow_redirects=False),
        enforce_manual_cooldown=False,
    ))
    return result, requests


class S202ResultTests(TestCase):
    def test_personal_result_page_read_is_one_get_only(self):
        def handler(req):
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.url.path, SELECTED_PAGE)
            self.assertEqual(req.headers["referer"],
                             "https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action")
            self.assertIn("SYNTHETIC_ONLY", req.headers["cookie"])
            return httpx.Response(
                200, content=selected_html(),
                headers={"content-type": "text/html; charset=GBK"})
        result, requests = call(handler)
        self.assertEqual(result.code, "page_observed")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result.recognized_tables, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].course_code, "TEST_COURSE")
        self.assertIn("不能据此认定", result.message)
        self.assertNotIn("TEST_COURSE", result.message)

    def test_empty_result_page_does_not_confirm_no_enrollment(self):
        html = "<html><body><h2>选课结果</h2></body></html>"
        result = parse_s202_readback(html)
        self.assertEqual(result.code, "page_observed")
        self.assertEqual(result.table_count, 0)
        self.assertEqual(result.candidates, ())
        self.assertNotIn("未选中", result.message)

    def test_expired_200_is_rejected(self):
        result, requests = call(lambda req: httpx.Response(
            200, content="凭证已失效".encode("gbk"),
            headers={"content-type": "text/html; charset=GBK"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_cross_origin_redirect_is_not_followed(self):
        result, requests = call(lambda req: httpx.Response(
            302, headers={"location": "https://authserver.ncwu.edu.cn"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(requests), 1)

    def test_429_is_not_retried(self):
        result, requests = call(lambda req: httpx.Response(
            429, headers={"Retry-After": "120"}))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(requests), 1)

    def test_invalid_host_blocks_before_network(self):
        result, requests = call(
            lambda req: self.fail("network must be blocked"),
            config={"base_url": "https://jwmis.ncwu.edu.cn.evil.example"})
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(requests, [])

    def test_missing_cookie_blocks_before_network(self):
        result, requests = call(
            lambda req: self.fail("network must be blocked"), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(requests, [])

    def test_arbitrary_table_is_not_a_selected_course(self):
        parsed = parse_s202_readback(
            "<html><h2>选课结果</h2><table><tr><th>课程</th><th>状态</th></tr>"
            "<tr><td>TEST_COURSE</td><td>成功</td></tr></table></html>")
        self.assertEqual(parsed.code, "page_observed")
        self.assertEqual(parsed.recognized_tables, 0)
        self.assertEqual(parsed.candidates, ())

    def test_duplicated_identity_column_is_not_trusted(self):
        parsed = parse_s202_readback(
            "<html><h2>选课结果</h2><table><tr><th>课程代码</th><th>课程代码</th>"
            "<th>教学班号</th></tr><tr><td>A</td><td>B</td>"
            "<td>C</td></tr></table></html>")
        self.assertEqual(parsed.code, "page_observed")
        self.assertEqual(parsed.recognized_tables, 0)
        self.assertEqual(parsed.candidates, ())

    def test_login_page_http200_is_not_mistaken_for_result_page(self):
        page = ("<html><body><h1>选课结果</h1><form>"
                "<input type='password' name='password'></form></body></html>")
        self.assertEqual(parse_s202_readback(page).code, "expired")

    def test_unrelated_200_html_is_not_an_enrollment_page(self):
        self.assertEqual(parse_s202_readback(
            "<html><body><h1>教学服务</h1></body></html>").code,
            "unrecognized")

    def test_html_without_document_root_is_not_accepted(self):
        self.assertEqual(parse_s202_readback("some unrelated text").code,
                         "unrecognized")


if __name__ == "__main__":
    import unittest
    unittest.main()
