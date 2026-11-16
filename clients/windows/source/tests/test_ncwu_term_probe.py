"""Offline mock tests: observed no-body NCWU YearTerm response is text/plain JSON."""
import asyncio
from contextlib import asynccontextmanager
from unittest import TestCase
from unittest.mock import patch

import httpx

from core.ncwu_term_probe import PATH, TermProbeResult, probe_known_year_term, validate_term_contract
from core.native_timetable import read_known_timetable, validate_timetable_contract
from core.native_login import validate_login_contract


def config():
    return {
        "base_url": "https://jwmis.ncwu.edu.cn",
        "auth": {"cookie": ""},
        "known_year_term": {
            "verified": True,
            "method": "POST",
            "path": PATH,
            "request_mode": "empty",
        },
    }


class Budget:
    def __init__(self):
        self.count = 0
        self.cooldowns = []

    @asynccontextmanager
    async def request(self):
        self.count += 1
        yield

    async def impose_retry_after(self, seconds):
        self.cooldowns.append(seconds)


def execute(cfg, handler, cookie="SID=LOCAL_SESSION"):
    budget = Budget()
    sent = []
    def respond(request):
        sent.append(request)
        return handler(request)
    result = asyncio.run(probe_known_year_term(
        cfg, session_cookie=cookie, environment={},
        http_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), follow_redirects=False),
        budget_factory=lambda config, origin: budget,
    ))
    return result, sent, budget


class TermProbeTests(TestCase):
    def test_real_observed_text_plain_json_shape_is_accepted(self):
        response = {
            "xn": "2026-2027", "xqM": "1", "xqName": "term",
            "jwUserYeartermId": "PRIVATE_SCOPE", "userCode": "PRIVATE_STUDENT",
            "docStatus": "present",
        }
        result, sent, budget = execute(
            config(), lambda req: httpx.Response(
                200, json=response, headers={"content-type": "text/plain; charset=UTF-8"}))
        self.assertEqual(result.code, "shape_observed")
        self.assertEqual(result.fields_matched, 5)
        self.assertEqual(budget.count, 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].method, "POST")
        self.assertEqual(sent[0].url.path, PATH)
        self.assertEqual(sent[0].content, b"")
        self.assertEqual(sent[0].headers["cookie"], "SID=LOCAL_SESSION")
        self.assertNotIn("PRIVATE", str(result) + result.message)
        self.assertIn("尚不能", result.message)

    def test_default_unverified_blocks_without_transport(self):
        cfg = config()
        cfg["known_year_term"]["verified"] = False
        result, sent, budget = execute(
            cfg, lambda req: (_ for _ in ()).throw(AssertionError("network")))
        self.assertEqual(result.code, "route_unverified")
        self.assertEqual(sent, [])

    def test_no_session_disallows_request(self):
        result, sent, budget = execute(config(), lambda req: httpx.Response(200), cookie="")
        self.assertEqual(result.code, "missing_session")
        self.assertEqual(sent, [])

    def test_request_with_params_rejected(self):
        cfg = config()
        cfg["known_year_term"]["request_fields"] = {"token": "STALE"}
        self.assertEqual(validate_term_contract(cfg), "invalid_contract")
        cfg = config()
        cfg["base_url"] = "https://jwmis.ncwu.edu.cn.bad.invalid"
        self.assertEqual(validate_term_contract(cfg), "invalid_contract")

    def test_login_html_200_is_not_identity(self):
        result, _, _ = execute(
            config(), lambda req: httpx.Response(
                200, content=b"<html>login</html>",
                headers={"content-type": "text/html"}))
        self.assertEqual(result.code, "invalid_response")

    def test_blank_or_missing_user_marker_rejected(self):
        response = {
            "xn": "2026-2027", "xqM": "1", "xqName": "term",
            "jwUserYeartermId": "scope", "userCode": "",
        }
        result, _, _ = execute(config(), lambda req: httpx.Response(
            200, json=response, headers={"content-type": "text/plain"}))
        self.assertEqual(result.code, "invalid_response")

    def test_302_is_session_expired(self):
        result, sent, _ = execute(config(), lambda req: httpx.Response(
            302, headers={"location": "https://authserver.ncwu.edu.cn/"}))
        self.assertEqual(result.code, "expired")
        self.assertEqual(len(sent), 1)

    def test_429_imposes_backoff_and_does_not_retry(self):
        result, sent, budget = execute(config(), lambda req: httpx.Response(
            429, headers={"retry-after": "65"}))
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(sent), 1)
        self.assertEqual(budget.cooldowns, [65.0])

    def test_captured_param_param2_are_not_replayed_as_static_timetable_body(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "known_timetable": {
                   "verified": True, "method": "POST",
                   "path": "/hsjw//wap/mycourseschedule.action",
                   "body_mode": "form",
                   "request_fields": {"param": "old_dynamic", "param2": "old_dynamic2"},
               }}
        self.assertEqual(validate_timetable_contract(cfg), "dynamic_request_unsupported")

    def test_school_login_variants_cannot_be_conflated(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn",
               "protocol": {"verified": True, "identity_path": "/identity",
                            "identity_selector": "#scope", "identity_referer": "/"},
               "auth": {"login": {
                   "mode": "direct_form", "verified": True,
                   "method": "POST", "path": "/hsjw/cas/logon.action",
                   "username_field": "username", "password_field": "password",
                   "static_fields": {},
               }}}
        self.assertEqual(validate_login_contract(cfg), "login_variant_unverified")
        cfg["auth"]["login"]["contract_variant"] = "signed_20261114"
        self.assertEqual(validate_login_contract(cfg), "signed_variant_unsupported")
        cfg["auth"]["login"]["contract_variant"] = "plain_20261103"
        self.assertEqual(validate_login_contract(cfg), "loginmethod_unverified")
        cfg["auth"]["login"]["static_fields"]["loginmethod"] = "TEST_ONLY"
        self.assertIsNone(validate_login_contract(cfg))


if __name__ == "__main__":
    import unittest
    unittest.main()
