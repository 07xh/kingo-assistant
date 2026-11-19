"""NCWU school-page readback from *observed* 2026-11-03 official routes.

This is an EXE-owned, one-shot, HTTPS read-only HTTP transport for the user's
already discovered timetable entry and selected-course result HTML pages.
It does not assert that a page 200 proves login or enrollment success; HTML
shape matching is distinct from identity or elective-catalog verification.
No request replay, browser cookie import, login automation or enrollment POST.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx

from core.client import KingoClient
from core.native_timetable import _own_cookie
from core.ncwu_known_result_candidates import (
    HistoricalClassCandidate, inspect_known_result_candidates,
)
from core.request_budget import build_verified_budget

HOST = "jwmis.ncwu.edu.cn"
PAGES = {
    "timetable_entry": "/hsjw/student/xkjg.wdkb.jsp",
    "selected_results": "/hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp",
}

_MAX_HTML_BYTES = 512 * 1024


@dataclass(frozen=True)
class KnownSchoolPageResult:
    code: str
    page_kind: str
    table_count: int = 0
    row_count: int = 0
    candidates: tuple[HistoricalClassCandidate, ...] = ()
    recognized_identity_tables: int = 0

    @property
    def message(self) -> str:
        if self.code == "shape_observed":
            kind = "本人课表入口" if self.page_kind == "timetable_entry" else "已选课程结果"
            return (
                f"EXE 已读取华水{kind}页面：识别到 {self.table_count} 个表格、"
                f"{self.row_count} 条表格行。"
                f"其中 {self.recognized_identity_tables} 张表有内部教学班字段，"
                f"可查看 {len(self.candidates)} 条 kcdm/skbjdm 候选记录。"
                "不能据此认定身份已独立验证、选课成功或仍有名额。"
            )
        return {
            "route_unverified": "旧 HAR 已确认网页路径，但当前页面请求契约尚未核验；没有联网",
            "query_unverified": "该网页在旧 HAR 中有 params 查询参数；取值已脱敏，不能猜测或复用，未联网",
            "invalid_contract": "读取配置与已知华水只读页面不一致；未联网",
            "missing_session": "EXE 缺少本人教务登录会话；浏览器已登录不代表软件已登录",
            "policy_unverified": "没有经过核实的学校请求配额；未访问教务",
            "expired": "页面要求登录或重定向；会话可能过期",
            "restricted": "教务服务拒绝本次读取，已停止",
            "invalid_response": "返回数据不像之前抓到的官方页面；不能作为已选记录",
            "unavailable": "该官方页面本次读取失败，未重试",
        }[self.code]


def validate_known_page_contract(config: Mapping, page_kind: str) -> str | None:
    """Only the exact previously observed school routes are candidates."""
    if page_kind not in PAGES:
        return "invalid_contract"
    base = config.get("base_url")
    try:
        parts = urlsplit(base)
        if (parts.scheme != "https" or parts.hostname != HOST
                or parts.port not in (None, 443)
                or parts.path not in ("", "/") or parts.query or parts.fragment
                or parts.username is not None or parts.password is not None):
            return "invalid_contract"
    except (ValueError, TypeError):
        return "invalid_contract"
    pages = config.get("known_school_pages")
    target = pages.get(page_kind) if isinstance(pages, dict) else None
    if not isinstance(target, dict) or target.get("verified") is not True:
        return "route_unverified"
    if target.get("method") != "GET" or target.get("path") != PAGES[page_kind]:
        return "invalid_contract"
    # Earlier report shows a redacted 'params' query on the selected-results
    # page. Do not manufacture the opaque value or persist it in YAML.
    if page_kind == "selected_results" and target.get("query_verified") is not True:
        return "query_unverified"
    if target.get("query_verified") is not True:
        return "route_unverified"
    # The current reader supports *only* queryless requests that have been
    # manually verified to be complete. Dynamic params require a separate
    # permission-reviewed builder, never HAR token replay.
    if target.get("query_mode") != "none" or target.get("query_fields") not in (None, {}):
        return "query_unverified"
    return None


class _TableShape(HTMLParser):
    """Counts only table structure; does not store raw HTML or student data."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = 0
        self.rows = 0
        self.header_names: set[str] = set()
        self._in_th = False
        self._buf: list[str] = []
        self._size = 0

    def handle_starttag(self, tag: str, attrs):
        if tag == "table":
            self.tables += 1
        elif tag == "tr":
            self.rows += 1
        elif tag == "th":
            self._in_th = True
            self._buf = []
            self._size = 0

    def handle_data(self, data: str):
        if self._in_th and self._size < 80:
            self._buf.append(data[:80-self._size])
            self._size += len(data[:80-self._size])

    def handle_endtag(self, tag: str):
        if tag == "th" and self._in_th:
            label = "".join(self._buf).strip().replace(" ", "")
            if label in {"课程", "任课教师", "选课状态", "上课时间地点", "学分",
                         "上课班级代码", "修读性质", "总学时"}:
                self.header_names.add(label)
            self._in_th = False
            self._buf = []
        elif tag == "table":
            self._in_th = False

    def matches(self, page_kind: str, raw_text: str) -> bool:
        if page_kind == "selected_results":
            return (self.tables > 0
                    and {"课程", "选课状态"}.issubset(self.header_names))
        # The first entry has academic year/semester inputs, not a known
        # unique personal-identity marker. This is only structural evidence.
        return (self.tables > 0 and ("学年" in raw_text or "学期" in raw_text)
                and "登录密码" not in raw_text)


async def read_known_school_page(
    config: Mapping, page_kind: str, *,
    session_cookie: str | None = None,
    environment: Mapping[str, str] | None = None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
) -> KnownSchoolPageResult:
    error = validate_known_page_contract(config, page_kind)
    if error is not None:
        return KnownSchoolPageResult(error, page_kind)
    cookie = _own_cookie(config, session_cookie, environment)
    if not cookie:
        return KnownSchoolPageResult("missing_session", page_kind)
    try:
        budget = budget_factory(dict(config), ("https", HOST))
    except Exception:
        return KnownSchoolPageResult("policy_unverified", page_kind)
    if http_factory is None:
        http_factory = lambda: httpx.AsyncClient(
            follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
    try:
        async with http_factory() as client:
            for token in cookie.split(";"):
                if "=" not in token:
                    return KnownSchoolPageResult("invalid_contract", page_kind)
                key, value = token.strip().split("=", 1)
                if (not key or not value or any(ch.isspace() for ch in key)):
                    return KnownSchoolPageResult("invalid_contract", page_kind)
                client.cookies.set(key, value, domain=HOST, path="/")
            async with budget.request():
                response = await client.get(
                    "https://" + HOST + PAGES[page_kind],
                    follow_redirects=False, headers={"Accept": "text/html"},
                )
                if response.status_code == 429:
                    after = KingoClient._retry_after_seconds(response.headers.get("Retry-After"))
                    await budget.impose_retry_after(60.0 if after is None else after)
                    return KnownSchoolPageResult("restricted", page_kind)
            if response.status_code == 401 or 300 <= response.status_code < 400:
                return KnownSchoolPageResult("expired", page_kind)
            if response.status_code == 403:
                return KnownSchoolPageResult("restricted", page_kind)
            if response.status_code != 200:
                return KnownSchoolPageResult("unavailable", page_kind)
            if len(response.content) > _MAX_HTML_BYTES:
                return KnownSchoolPageResult("invalid_response", page_kind)
            mime = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if mime not in ("text/html", "application/xhtml+xml"):
                return KnownSchoolPageResult("invalid_response", page_kind)
            charset = "utf-8"
            for param in response.headers.get("content-type", "").split(";")[1:]:
                if param.strip().lower().startswith("charset="):
                    charset = param.split("=", 1)[1].strip().strip('"').lower()
            # Interpret only a small, known-safe set of legacy encodings.
            if charset not in ("utf-8", "utf8", "gbk", "gb2312", "gb18030"):
                return KnownSchoolPageResult("invalid_response", page_kind)
            try:
                decoded = response.content.decode(charset, errors="strict")
            except (UnicodeError, LookupError):
                return KnownSchoolPageResult("invalid_response", page_kind)
            parser = _TableShape()
            parser.feed(decoded)
            inspected = (inspect_known_result_candidates(decoded)
                         if page_kind == "selected_results" else None)
            # The Nov 3 firsthand response uses TD-only table headings, while
            # _TableShape originally counted only TH headings. A strict
            # recognized paired-ID table is an alternate shape, NOT proof of
            # enrollment or permission to ignore the request contract.
            matched = parser.matches(page_kind, decoded)
            if inspected is not None and inspected.recognized_tables:
                matched = True
            if not matched:
                return KnownSchoolPageResult("invalid_response", page_kind)
            return KnownSchoolPageResult(
                "shape_observed", page_kind, parser.tables, parser.rows,
                candidates=inspected.candidate_rows if inspected is not None else (),
                recognized_identity_tables=(
                    inspected.recognized_tables if inspected is not None else 0),
            )
    except Exception:
        return KnownSchoolPageResult("unavailable", page_kind)
