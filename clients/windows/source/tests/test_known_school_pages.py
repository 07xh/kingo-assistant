"""No-network tests for the previously captured school HTML readback routes."""
import asyncio
from contextlib import asynccontextmanager
from unittest import TestCase
from unittest.mock import patch

import httpx

from core.known_school_pages import (
    PAGES, validate_known_page_contract, read_known_school_page,
)
from core.native_login import validate_login_contract


def config(kind="selected_results"):
    return {
        "base_url": "https://jwmis.ncwu.edu.cn",
        "known_school_pages": {kind: {
            "verified": True, "method": "GET", "path": PAGES[kind],
            "query_verified": True, "query_mode": "none",
            "query_fields": {},
        }},
        "auth": {"cookie": ""},
    }


class Budget:
    def __init__(self):
        self.requests = 0
        self.backoffs = []

    @asynccontextmanager
    async def request(self):
        self.requests += 1
        yield

    async def impose_retry_after(self, seconds):
        self.backoffs.append(seconds)


def call(cfg, kind, handler, cookie="SID=SELF"):
    requests = []
    budget = Budget()
    def respond(req):
        requests.append(req)
        return handler(req)
    result = asyncio.run(read_known_school_page(
        cfg, kind, session_cookie=cookie,
        environment={},
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), follow_redirects=False),
        budget_factory=lambda config, origin: budget,
    ))
    return result, requests, budget


class SchoolPageTests(TestCase):
    def test_successful_selected_results_structure_not_successful_enrollment(self):
        html = (b"<html><table><tr><th>\xe8\xaf\xbe\xe7\xa8\x8b</th>"
                b"<th>\xe9\x80\x89\xe8\xaf\xbe\xe7\x8a\xb6\xe6\x80\x81</th></tr>"
                b"<tr><td>PRIVATE_COURSE</td><td>PRIVATE_RECORD</td></tr>"
                b"</table></html>")
        result, requests, budget = call(
            config(), "selected_results", lambda req: httpx.Response(
                200, content=html, headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "shape_observed")
        self.assertEqual(result.table_count, 1)
        self.assertEqual(result.row_count, 2)
        self.assertIn("不能据此认定", result.message)
        self.assertNotIn("PRIVATE", str(result) + result.message)
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(requests[0].url),
                         "https://jwmis.ncwu.edu.cn" + PAGES["selected_results"])
        self.assertEqual(requests[0].headers["cookie"], "SID=SELF")
        self.assertEqual(budget.requests, 1)

    def test_actual_td_heading_with_internal_fields_yields_candidate_not_confirmation(self):
        html = (
            "<html><body><table>"
            "<tr><td>课程</td><td>上课班级 代码</td><td>选课 状态</td></tr>"
            "<tr><td>虚构课程</td><td>展示班号</td><td>未知状态</td>"
            '<td name="kcdm" id="kcdm_0">COURSE101</td>'
            '<td name="skbjdm" id="skbjdm_0">CLASS-PRIVATE</td></tr>'
            "</table></body></html>"
        )
        result, requests, _ = call(
            config(), "selected_results", lambda req: httpx.Response(
                200, text=html, headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "shape_observed")
        self.assertEqual(result.recognized_identity_tables, 1)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].internal_class_id, "CLASS-PRIVATE")
        self.assertNotIn("CLASS-PRIVATE", result.message)
        self.assertEqual(len(requests), 1)

    def test_unverified_query_does_not_send(self):
        cfg = config()
        cfg["known_school_pages"]["selected_results"]["query_verified"] = False
        result, requests, budget = call(cfg, "selected_results",
                                       lambda r: (_ for _ in ()).throw(
                                           AssertionError("network")))
        self.assertEqual(result.code, "query_unverified")
        self.assertEqual(requests, [])
        self.assertEqual(budget.requests, 0)

    def test_refuses_query_token_replay(self):
        cfg = config()
        cfg["known_school_pages"]["selected_results"]["query_mode"] = "captured_token"
        self.assertEqual(validate_known_page_contract(cfg, "selected_results"),
                         "query_unverified")

    def test_timetable_page_read_is_separate_from_selected_results(self):
        cfg = config("timetable_entry")
        html = "<html><form>学年 学期</form><table><tr><th>个人课表</th></tr></table></html>"
        result, requests, budget = call(
            cfg, "timetable_entry", lambda req: httpx.Response(
                200, text=html, headers={"content-type": "text/html; charset=utf-8"}))
        self.assertEqual(result.code, "shape_observed")
        self.assertNotIn("已选成功", result.message)
        self.assertEqual(len(requests), 1)

    def test_invalid_host_does_not_connect(self):
        cfg = config()
        cfg["base_url"] = "https://jwmis.ncwu.edu.cn.evil.invalid/"
        result, reqs, budget = call(cfg, "selected_results",
                                    lambda r: (_ for _ in ()).throw(
                                        AssertionError("network")))
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(reqs, [])

    def test_expired_redirect_does_not_follow(self):
        result, reqs, budget = call(config(), "selected_results",
                                    lambda req: httpx.Response(
                                        302, headers={"location":
                                             "https://authserver.ncwu.edu.cn/login"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(reqs), 1)

    def test_login_html_200_rejected(self):
        result, reqs, budget = call(config(), "selected_results",
                                    lambda req: httpx.Response(
                                        200, text="<html>账号 密码</html>",
                                        headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "invalid_response")

    def test_http429_backoff(self):
        result, reqs, budget = call(config(), "selected_results",
                                    lambda req: httpx.Response(
                                        429, headers={"Retry-After": "72"}))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(budget.backoffs, [72.0])

    def test_ncwulogin_requires_observed_loginmethod(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "protocol": {
                   "verified": True, "identity_path": "/identity",
                   "identity_selector": "#user", "identity_referer": "/",
               },
               "auth": {"login": {
                   "mode": "direct_form", "verified": True,
                   "method": "POST", "path": "/hsjw/cas/logon.action",
                   "username_field": "username", "password_field": "password",
                   "static_fields": {},
               }}}
        self.assertEqual(validate_login_contract(cfg), "login_variant_unverified")
        cfg["auth"]["login"]["contract_variant"] = "plain_20261103"
        self.assertEqual(validate_login_contract(cfg), "loginmethod_unverified")
        cfg["auth"]["login"]["static_fields"]["loginmethod"] = "MANUALLY_VERIFIED_ONLY"
        self.assertIsNone(validate_login_contract(cfg))


if __name__ == "__main__":
    import unittest
    unittest.main()
