"""EXE-native one-shot read of the *observed* NCWU personal timetable API.

2026-11-11 user HAR established a JSON response from the literal POST route
/hsjw//wap/mycourseschedule.action. It did NOT establish the request form
parameters or an elective catalog contract. This implementation therefore
requires an independently reviewed request mapping and authorized session,
performs at most one bounded POST, and reports privacy-safe schema counts only.

No browser control, school login, request replay, enrollment submit or polling.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from core.client import KingoClient
from core.ncwu_timetable_schema import inspect_timetable_json, TimetableSchemaError
from core.request_budget import build_verified_budget

OFFICIAL_TIMETABLE_HOST = "jwmis.ncwu.edu.cn"
OBSERVED_TIMETABLE_PATH = "/hsjw//wap/mycourseschedule.action"
_SECRET_HINTS = (
    "cookie", "session", "token", "ticket", "password", "passwd",
    "secret", "sign", "authorization", "csrf", "captcha", "otp", "verify",
)


@dataclass(frozen=True)
class TimetableReadResult:
    code: str
    weekday_entries: int = 0
    auxiliary_entries: int = 0

    @property
    def message(self) -> str:
        return {
            "route_unverified": "已知华水课表接口尚缺少经过核验的请求参数；未访问学校",
            "invalid_contract": "课表请求配置无效或包含可能的会话字段；未访问学校",
            "missing_session": "EXE 尚无自己的教务会话；浏览器登录不能自动算作软件登录",
            "policy_unverified": "没有经过核实的华水请求预算；未访问学校",
            "expired": "教务服务要求重新认证；未读取课表",
            "restricted": "学校拒绝访问或触发限制；已停止本次读取",
            "invalid_response": "服务器返回内容与之前抓到的课表 JSON 不符，未解释为课程列表",
            "unavailable": "本次课表接口读取失败；没有据此认定选课功能可用",
            "schema_matched": (
                f"已从 EXE 请求华水课表接口，识别到 {self.weekday_entries} 条星期项目、"
                f"{self.auxiliary_entries} 条附加项目；这是本人课表格式，"
                "不是选课目录或余量，尚未独立验证当前 EXE 会话身份"
            ),
        }[self.code]


def validate_timetable_contract(config: Mapping) -> str | None:
    """Offline-only check. Does not assert that a self-edited flag proves access."""
    route = config.get("known_timetable")
    if not isinstance(route, dict) or route.get("verified") is not True:
        return "route_unverified"
    base = config.get("base_url")
    if not isinstance(base, str):
        return "invalid_contract"
    try:
        host = urlsplit(base)
        if (host.scheme != "https" or host.hostname != OFFICIAL_TIMETABLE_HOST
                or host.port not in (None, 443) or host.username is not None
                or host.password is not None or host.path not in ("", "/")
                or host.query or host.fragment):
            return "invalid_contract"
    except ValueError:
        return "invalid_contract"
    # Do not treat the observed HTTP 200 as proof of the request mapping.
    if route.get("method") != "POST" or route.get("path") != OBSERVED_TIMETABLE_PATH:
        return "invalid_contract"
    body_mode = route.get("body_mode")
    fields = route.get("request_fields")
    if body_mode not in ("form", "json") or not isinstance(fields, dict) or len(fields) > 20:
        return "invalid_contract"
    for key, value in fields.items():
        if (not isinstance(key, str) or not key or len(key) > 64
                or any(c in key for c in "\r\n=;&")
                or any(hint in key.casefold() for hint in _SECRET_HINTS)
                or not isinstance(value, str) or len(value) > 256
                or any(c in value for c in "\r\n")):
            return "invalid_contract"
    return None


def _own_cookie(config: Mapping, session_cookie: str | None,
                environment: Mapping[str, str] | None) -> str:
    if session_cookie is not None:
        raw = session_cookie
    else:
        env = os.environ if environment is None else environment
        auth = config.get("auth")
        raw = env.get("KINGO_COOKIE") or (
            auth.get("cookie") if isinstance(auth, dict) else "")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        return ""
    # A cookie header comes from this app's authorized session store.
    # Never accept CRLF or arbitrary header bytes.
    if any(c in raw for c in "\r\n"):
        return ""
    return raw


async def read_known_timetable(
    config: Mapping, *,
    session_cookie: str | None = None,
    environment: Mapping[str, str] | None = None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable[[dict, tuple[str, str]], object] = build_verified_budget,
) -> TimetableReadResult:
    """One bounded POST with exact observed route and explicit verified fields."""
    error = validate_timetable_contract(config)
    if error is not None:
        return TimetableReadResult(error)
    cookie = _own_cookie(config, session_cookie, environment)
    if not cookie:
        return TimetableReadResult("missing_session")
    try:
        budget = budget_factory(dict(config), ("https", OFFICIAL_TIMETABLE_HOST))
    except Exception:
        return TimetableReadResult("policy_unverified")
    route = config["known_timetable"]
    form = dict(route["request_fields"])
    url = "https://" + OFFICIAL_TIMETABLE_HOST + OBSERVED_TIMETABLE_PATH
    if http_factory is None:
        http_factory = lambda: httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
    try:
        async with http_factory() as client:
            for component in cookie.split(";"):
                if "=" not in component:
                    return TimetableReadResult("invalid_contract")
                key, value = component.strip().split("=", 1)
                if not key or not value or any(ch.isspace() for ch in key):
                    return TimetableReadResult("invalid_contract")
                client.cookies.set(key, value, domain=OFFICIAL_TIMETABLE_HOST, path="/")
            async with budget.request():
                kwargs = {"data": form} if route["body_mode"] == "form" else {"json": form}
                response = await client.post(url, **kwargs, follow_redirects=False)
                if response.status_code == 429:
                    backoff = KingoClient._retry_after_seconds(
                        response.headers.get("Retry-After"))
                    await budget.impose_retry_after(60.0 if backoff is None else backoff)
                    return TimetableReadResult("restricted")
            if response.status_code == 401 or 300 <= response.status_code < 400:
                return TimetableReadResult("expired")
            if response.status_code == 403:
                return TimetableReadResult("restricted")
            if response.status_code != 200:
                return TimetableReadResult("unavailable")
            if len(response.content) > 1024 * 1024:
                return TimetableReadResult("invalid_response")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in ("application/json", "text/json") and not content_type.endswith("+json"):
                return TimetableReadResult("invalid_response")
            try:
                raw = response.content.decode("utf-8-sig", errors="strict")
                shape = inspect_timetable_json(raw)
            except (UnicodeError, TimetableSchemaError):
                return TimetableReadResult("invalid_response")
            return TimetableReadResult(
                "schema_matched", shape.weekday_entries, shape.auxiliary_entries)
    except Exception:
        # Never leak cookies, URL query, personal timetable or raw HTTP output.
        return TimetableReadResult("unavailable")
