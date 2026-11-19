"""Synthetic-only contract tests for the observed first-party signed login.

Never use a real NCWU account, password, HAR token or server in CI.
"""
import asyncio
import base64
import json
from unittest import TestCase

import httpx

from core.ncwu_legacy_encoder import encode_legacy_strenc
from core.ncwu_signed_login import (
    _content,
    _form_bootstrap,
    _sign_login_form,
    login_ncwusigned,
    validate_ncwulogin_entry,
)

_ID = "1234567890ABCDEF1234567890ABCDEF"
_TEMP_KEY = "12345678901234567890123"
_TEST_TIME = "2026-11-16 20:00:00"
_ACCOUNT = "TEST_STUDENT_101"
_SECRET = "TEST_ONLY_PASSWORD"


def config():
    return {"base_url": "https://jwmis.ncwu.edu.cn"}


def login_html(*, captcha=False):
    return (
        "<html><script>"
        f'var _sessionid = "{_ID}";'
        f'var _ssessionid = "{_ID}";'
        'var yzmbt = "' + ("1" if captcha else "0") + '";'
        '</script><input id="hid_flag" type="hidden" value="'
        + ("0" if captcha else "1") + '" /></html>'
    )


def mock_login(*, status="200", term_user=_ACCOUNT, captcha=False, redirect=False,
               deny=False):
    sent = []

    def handler(req):
        sent.append(req)
        path = req.url.path
        if path == "/hsjw/cas/login.action":
            if redirect:
                return httpx.Response(
                    302, headers={"location": "https://authserver.ncwu.edu.cn/"})
            return httpx.Response(
                200, text=login_html(captcha=captcha),
                headers={"content-type": "text/html; charset=utf-8",
                         "set-cookie": "JSESSIONID=BOOTSTRAP; Path=/; HttpOnly"})
        if path == "/hsjw/custom/js/SetKingoEncypt.jsp":
            return httpx.Response(
                200, content=b"var _deskey = '';",
                headers={"content-type": "text/javascript"})
        if path == "/hsjw/frame/homepage":
            method = req.url.params.get("method")
            if method == "getTempDeskey":
                text = _TEMP_KEY
            elif method == "getTempNowtime":
                text = _TEST_TIME
            else:
                raise AssertionError("unrecognized official temporary-value route")
            return httpx.Response(
                200, text=text, headers={"content-type": "text/plain"})
        if path == "/hsjw/cas/logon.action":
            if deny:
                return httpx.Response(429, headers={"retry-after": "120"})
            assert req.method == "POST"
            assert b"username=" not in req.content
            assert _SECRET.encode() not in req.content
            form = dict(pair.split("=", 1) for pair in req.content.decode().split("&"))
            assert set(form) == {"params", "token", "timestamp", "deskey", "ssessionid"}
            assert form["timestamp"].replace("+", " ") == "2026-11-16+20%3A00%3A00".replace("+", " ")
            assert form["ssessionid"] == _ID
            return httpx.Response(
                200, json={"status": status, "message": "test", "result": ""},
                headers={"content-type": "text/plain",
                         "set-cookie": "JSESSIONID=AUTHENTICATED; Path=/; HttpOnly"})
        if path == "/hsjw/jw/common/showYearTerm.action":
            assert req.method == "POST"
            assert req.content == b""
            assert "AUTHENTICATED" in req.headers["cookie"]
            return httpx.Response(
                200, json={
                    "xn": "2026-2027", "xqM": "1", "xqName": "semester",
                    "jwUserYeartermId": "PRIVATE_TERM_SCOPE",
                    "userCode": term_user,
                }, headers={"content-type": "text/plain"})
        raise AssertionError("unexpected network path " + path)

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                 follow_redirects=False)

    return factory, sent


class LegacyCompatibilityTests(TestCase):
    def test_original_legacy_des_vector(self):
        self.assertEqual(
            encode_legacy_strenc("abcd", "ABCD")[:8] != "", True)
        # Independently published E-E-E vector for the original PC-1 ordering.
        key1 = encode_legacy_strenc("abcd", "AB")
        self.assertEqual(len(key1), 16)
        self.assertEqual(encode_legacy_strenc("abcd", "1"), "4A60B51D4FD386C1")
        self.assertNotEqual(key1, encode_legacy_strenc("abcd", "CD"))

    def test_encryption_uses_fresh_ephemeral_key(self):
        form1 = _sign_login_form(
            _ACCOUNT, _SECRET, _ID, _ID, "", _TEMP_KEY, _TEST_TIME)
        form2 = _sign_login_form(
            _ACCOUNT, _SECRET, _ID, _ID, "", "99999999999999999999999", _TEST_TIME)
        self.assertNotEqual(form1["params"], form2["params"])
        self.assertEqual(form1["token"], form2["token"])
        self.assertNotIn(_SECRET, json.dumps(form1))
        self.assertNotIn(_ACCOUNT, json.dumps(form1))
        self.assertGreater(len(base64.b64decode(form1["params"])), 50)


class SignedLoginTests(TestCase):
    def sign_in(self, **kwargs):
        factory, sent = mock_login(**kwargs)
        result = asyncio.run(login_ncwusigned(
            config(), _ACCOUNT, _SECRET,
            http_factory=factory, enforce_local_cooldown=False))
        return result, sent

    def test_gbk_login_page_preserves_original_school_form(self):
        page = login_html().replace("</html>", "<p>登录验证</p></html>")
        response = httpx.Response(
            200, content=page.encode("gbk"),
            headers={"content-type": "text/html; charset=GBK"},
        )
        decoded = _content(response, max_bytes=512_000, mime=("text/html",))
        self.assertIn("登录验证", decoded)
        self.assertEqual(_form_bootstrap(decoded), (_ID, _ID))

    def test_happy_path_is_real_native_http_only_and_account_bound(self):
        result, sent = self.sign_in()
        self.assertEqual(result.code, "verified")
        self.assertTrue(result.verified)
        self.assertEqual(result.session_origin, "jwmis.ncwu.edu.cn")
        self.assertEqual(len(sent), 6)
        self.assertEqual(sum(req.method == "POST" for req in sent), 2)
        self.assertEqual([req.url.path for req in sent], [
            "/hsjw/cas/login.action",
            "/hsjw/custom/js/SetKingoEncypt.jsp",
            "/hsjw/frame/homepage", "/hsjw/frame/homepage",
            "/hsjw/cas/logon.action",
            "/hsjw/jw/common/showYearTerm.action",
        ])
        self.assertEqual(result.session_cookie, "JSESSIONID=AUTHENTICATED")
        self.assertNotIn(_SECRET, repr(result) + result.message)
        self.assertNotIn(_ACCOUNT, repr(result) + result.message)

    def test_mismatched_school_account_must_discard_cookie(self):
        result, sent = self.sign_in(term_user="OTHER_STUDENT")
        self.assertEqual(result.code, "identity_mismatch")
        self.assertFalse(result.verified)
        self.assertEqual(result.session_cookie, "")
        self.assertEqual(len(sent), 6)

    def test_school_captcha_stops_before_auth_request(self):
        result, sent = self.sign_in(captcha=True)
        self.assertEqual(result.code, "challenge_required")
        self.assertEqual(len(sent), 1)
        self.assertFalse(result.verified)

    def test_school_login_denial_not_retried(self):
        result, sent = self.sign_in(status="401")
        self.assertEqual(result.code, "login_rejected")
        self.assertEqual(len(sent), 5)
        self.assertFalse(result.verified)

    def test_429_stops_and_never_reads_yearterm(self):
        result, sent = self.sign_in(deny=True)
        self.assertEqual(result.code, "restricted")
        self.assertEqual(len(sent), 5)

    def test_cas_redirect_stops_without_following(self):
        result, sent = self.sign_in(redirect=True)
        self.assertEqual(result.code, "protocol_changed")
        self.assertEqual(len(sent), 1)

    def test_invalid_origin_does_not_access_network(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn.other.example"}
        self.assertEqual(validate_ncwulogin_entry(cfg), "invalid_contract")
        factory, sent = mock_login()
        result = asyncio.run(login_ncwusigned(
            cfg, _ACCOUNT, _SECRET,
            http_factory=factory, enforce_local_cooldown=False))
        self.assertEqual(result.code, "invalid_contract")
        self.assertEqual(len(sent), 0)

    def test_injection_like_password_is_rejected_before_network(self):
        factory, sent = mock_login()
        result = asyncio.run(login_ncwusigned(
            config(), _ACCOUNT, "unsafe&extra=1",
            http_factory=factory, enforce_local_cooldown=False))
        self.assertEqual(result.code, "invalid_credentials")
        self.assertEqual(len(sent), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
