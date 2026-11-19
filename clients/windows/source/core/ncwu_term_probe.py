"""EXE-native, one-shot NCWU academic-year/term response probe.

Confirmed independently in the user's Nov 03 and Nov 14 authenticated HARs:
POST https://jwmis.ncwu.edu.cn/hsjw/jw/common/showYearTerm.action with no
form body; text/plain containing a small JSON object with an account marker
and academic-term fields. This is NOT enrollment and NOT an account-identity
proof by itself. Never return the school's private field values.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from core.client import KingoClient
from core.native_timetable import _own_cookie
from core.request_budget import build_verified_budget

HOST = "jwmis.ncwu.edu.cn"
PATH = "/hsjw/jw/common/showYearTerm.action"
_REQUIRED = ("xn", "xqM", "xqName", "jwUserYeartermId", "userCode")


@dataclass(frozen=True)
class TermProbeResult:
    code: str
    fields_matched: int = 0

    @property
    def message(self) -> str:
        if self.code == "shape_observed":
            return (
                "EXE 已读取华水学年学期接口：返回结构中有学期及用户作用域字段；"
                "不显示本人身份数据。这只是一次账号相关页面的结构证据，"
                "尚不能单凭此证明登录身份、课程目录或选课成功。"
            )
        if self.code == "identity_verified":
            return (
                "EXE 独立学期接口返回的 userCode 与本次登录输入完全一致；"
                "仅该软件会话已完成账号一致性校验，不代表选课接口可用。"
            )
        return {
            "route_unverified": "旧 HAR 两次确认了学期接口结构，EXE 查询协议尚未核验；未联网",
            "identity_mismatch": "学期接口返回的账号标识与本次登录账号不一致；不保留 EXE 登录会话",
            "invalid_identity_claim": "本次登录账号标识无效，不能核验会话身份；未访问学校",
            "invalid_contract": "学期接口配置错误；未向学校发送请求",
            "missing_session": "EXE 尚无本人教务会话；浏览器登录不等于软件登录",
            "policy_unverified": "学校允许的请求频率未核验；没有访问接口",
            "expired": "学期接口提示需要重新认证；没有获得结构证据",
            "restricted": "学校拒绝或限制本次查询，已停止",
            "invalid_response": "返回结构不同于前两次真实抓包；未认定身份或学期",
            "unavailable": "学期接口读取失败；没有自动重试",
        }[self.code]


def validate_term_contract(config: Mapping) -> str | None:
    contract = config.get("known_year_term")
    if not isinstance(contract, dict) or contract.get("verified") is not True:
        return "route_unverified"
    try:
        host = urlsplit(config.get("base_url"))
        if (host.scheme != "https" or host.hostname != HOST
                or host.port not in (None, 443)
                or host.path not in ("", "/") or host.query or host.fragment
                or host.username is not None or host.password is not None):
            return "invalid_contract"
    except (TypeError, ValueError):
        return "invalid_contract"
    # Static/request values, dynamic signatures and alternate paths are
    # deliberately unsupported by this exact old HAR-backed action.
    if (contract.get("method") != "POST"
            or contract.get("path") != PATH
            or contract.get("request_mode") != "empty"
            or contract.get("request_fields") not in (None, {})):
        return "invalid_contract"
    return None


async def probe_known_year_term(
    config: Mapping, *,
    session_cookie: str | None = None,
    environment: Mapping[str, str] | None = None,
    expected_user_code: str | None = None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
) -> TermProbeResult:
    error = validate_term_contract(config)
    if error is not None:
        return TermProbeResult(error)
    if expected_user_code is not None and (
            not isinstance(expected_user_code, str)
            or not expected_user_code.strip()
            or expected_user_code != expected_user_code.strip()
            or len(expected_user_code) > 128):
        return TermProbeResult("invalid_identity_claim")
    cookie = _own_cookie(config, session_cookie, environment)
    if not cookie:
        return TermProbeResult("missing_session")
    try:
        budget = budget_factory(dict(config), ("https", HOST))
    except Exception:
        return TermProbeResult("policy_unverified")
    if http_factory is None:
        http_factory = lambda: httpx.AsyncClient(
            trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
    try:
        async with http_factory() as client:
            for component in cookie.split(";"):
                if "=" not in component:
                    return TermProbeResult("invalid_contract")
                key, value = component.strip().split("=", 1)
                if not key or not value or any(ch.isspace() for ch in key):
                    return TermProbeResult("invalid_contract")
                client.cookies.set(key, value, domain=HOST, path="/")
            async with budget.request():
                response = await client.post(
                    "https://" + HOST + PATH,
                    follow_redirects=False,
                    headers={"Accept": "text/plain,application/json"},
                )
                if response.status_code == 429:
                    pause = KingoClient._retry_after_seconds(
                        response.headers.get("Retry-After"))
                    await budget.impose_retry_after(60.0 if pause is None else pause)
                    return TermProbeResult("restricted")
            if response.status_code == 401 or 300 <= response.status_code < 400:
                return TermProbeResult("expired")
            if response.status_code == 403:
                return TermProbeResult("restricted")
            if response.status_code != 200:
                return TermProbeResult("unavailable")
            if len(response.content) > 8192:
                return TermProbeResult("invalid_response")
            ct = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ct not in ("text/plain", "application/json") and not ct.endswith("+json"):
                return TermProbeResult("invalid_response")
            try:
                parsed = json.loads(response.content.decode("utf-8-sig", "strict"))
            except (ValueError, UnicodeError, RecursionError):
                return TermProbeResult("invalid_response")
            if (not isinstance(parsed, dict)
                    or any(not isinstance(parsed.get(key), str)
                           or not parsed[key].strip()
                           or len(parsed[key]) > 100 for key in _REQUIRED)):
                return TermProbeResult("invalid_response")
            if expected_user_code is not None:
                # Compare only in memory, without logging or returning either
                # identifier. A result from somebody else's session must fail.
                if hmac.compare_digest(
                        parsed["userCode"].encode("utf-8"),
                        expected_user_code.encode("utf-8")):
                    return TermProbeResult("identity_verified", len(_REQUIRED))
                return TermProbeResult("identity_mismatch")
            return TermProbeResult("shape_observed", len(_REQUIRED))
    except Exception:
        return TermProbeResult("unavailable")
