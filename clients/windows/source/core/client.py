"""NCWU Kingosoft *read-only* client: authenticated session, strict URL scope,
static GBK query pre-encoding, fast row prefilter, and bounded retries.

The configured URL paths are NOT verified for NCWU. Requests are disabled until
protocol.verified=true and mandatory path/selector fields are supplied.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode, urljoin, urlsplit
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from .request_budget import SingleFlight, build_verified_budget


class Status(str, Enum):
    AVAILABLE = "发现余量，请在官网确认"
    FULL = "已满"
    UNKNOWN = "余量未知"
    MISSING = "课程未找到"
    AMBIGUOUS = "教学班不唯一"
    AUTH_EXPIRED = "登录已过期"
    RATE_LIMITED = "触发访问限制，已停止"
    READ_ERROR = "读取失败"


class ProtocolError(RuntimeError):
    """Invalid or unverified school response; do not guess business state."""


class AuthenticationError(ProtocolError):
    """The official site rejected or redirected the authorized session."""


class RateLimitError(ProtocolError):
    """429/403 requires stopping, not header spoofing or retrying."""


@dataclass(frozen=True)
class CourseItem:
    course_id: str
    course_name: str
    teacher: str
    remain: int | None
    class_id: str | None = None


def encode_gbk_form(pairs: list[tuple[str, str]]) -> bytes:
    """Encode once outside the polling loop; non-ASCII values are GBK-percent-encoded."""
    return urlencode([(str(k).encode("gbk"), str(v).encode("gbk"))
                      for k, v in pairs]).encode("ascii")


_ROW = re.compile(rb"<tr\b[^>]*>.*?</tr\s*>", re.IGNORECASE | re.DOTALL)
_TABLE = re.compile(rb"<table\b[^>]*>.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_TABLE_START = re.compile(rb"<table\b[^>]*>", re.IGNORECASE | re.DOTALL)
_ALERT = re.compile(r"<script\b|验证码|选课成功|非法访问|请先登录", re.IGNORECASE)
_INTEGER = re.compile(r"\d{1,5}")
_LOGIN = re.compile(r"登录超时|请先登录|重新登录|登录已失效|无权访问")


_HEADER_CHARSET = re.compile(r"charset\s*=\s*['\"]?([a-zA-Z0-9_-]+)", re.IGNORECASE)
_META_CHARSET = re.compile(rb"<meta\b[^>]*?\bcharset\s*=\s*['\"]?\s*([a-zA-Z0-9_-]+)", re.IGNORECASE)


def html_encoding(raw: bytes, content_type: str = "") -> str:
    """Choose a strict charset from trusted transport/meta hints; never ignore bytes.

    Legacy 青果 pages usually use GBK/GB2312, accepted by GB18030. Some
    modern pages declare UTF-8. Conflicting declarations are an error rather
    than an opportunity to silently corrupt course names/IDs.
    """
    def normalize(label: str) -> str:
        value = label.strip().lower().replace("_", "-")
        if value in ("gbk", "gb2312", "gb-2312", "gb18030", "gb-18030"):
            return "gb18030"
        if value in ("utf-8", "utf8"):
            return "utf-8"
        raise ProtocolError("课程页面声明了不支持的字符编码")

    header = _HEADER_CHARSET.search(content_type)
    meta = _META_CHARSET.search(raw[:16384])
    declared = []
    if header:
        declared.append(normalize(header.group(1)))
    if meta:
        declared.append(normalize(meta.group(1).decode("ascii")))
    if len(set(declared)) > 1:
        raise ProtocolError("HTTP 与页面字符编码声明冲突，停止读取")
    if declared:
        return declared[0]
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    # Historic NCWU browser pages are often GBK. This is a cautious default,
    # not proof that a particular school's current course page uses GBK.
    return "gb18030"


def decode_html(response: httpx.Response) -> str:
    codec = html_encoding(response.content, response.headers.get("content-type", ""))
    try:
        return response.content.decode(codec)
    except UnicodeError as error:
        raise ProtocolError("课程页面内容与声明编码不一致，停止读取") from error


def _column_map(config: dict) -> dict[str, int]:
    columns = config.get("columns", {})
    required = {"course_id", "course_name", "teacher", "remain"}
    allowed = required | {"class_id"}
    if (not isinstance(columns, dict) or not required.issubset(columns)
            or not set(columns).issubset(allowed) or any(
                not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 50
                for v in columns.values()
            ) or len(set(columns.values())) != len(columns)):
        raise ProtocolError("课程列索引无效或重复，需以真实表头核对")
    return columns


def parse_courses(raw: bytes, config: dict, course_ids: list[str] | None = None,
                  *, content_type: str = "") -> list[CourseItem]:
    """Parse only a candidate row when target IDs were provided.

    The table structure must be checked against an actual, authorized local
    page capture. An unrelated page is an ERROR, never a false "course full".
    """
    columns = _column_map(config)
    if not raw or len(raw) > 4_000_000:
        raise ProtocolError("选课页面为空或异常过大")
    codec = html_encoding(raw, content_type)
    try:
        raw.decode(codec)  # Strict validation of the entire capture, including skipped rows.
    except UnicodeError as error:
        raise ProtocolError("课程列表编码无效，拒绝有损解析") from error
    marker = config.get("table_marker")
    if marker:
        if not isinstance(marker, str) or not marker or len(marker) > 160:
            raise ProtocolError("table_marker 无效")
        # A page-global marker is NOT enough: only parse rows from the
        # exact table whose opening tag contains the verified marker.
        matching_tables = []
        needle = marker.encode(codec)
        for table in _TABLE.finditer(raw):
            opening = _TABLE_START.match(table.group(0))
            if opening is not None and needle in opening.group(0):
                matching_tables.append(table.group(0))
        if len(matching_tables) != 1:
            raise ProtocolError("课程表标识必须唯一且位于 table 起始标签；拒绝读取无关表格")
        raw = matching_tables[0]
    targets = set(course_ids or ())
    patterns = tuple(re.compile(re.escape(code.encode(codec)), re.IGNORECASE)
                     for code in targets if code)
    rows: list[CourseItem] = []
    sample_other_rows: list[bytes] = []
    examined_valid = False
    saw_table_row = False

    def parse_row(data: bytes) -> CourseItem | None:
        try:
            snippet = data.decode(codec)
        except UnicodeError as error:
            raise ProtocolError("选课行编码不完整") from error
        soup = BeautifulSoup(snippet, "lxml")
        tr = soup.find("tr")
        if tr is None:
            return None
        cells = tr.find_all("td", recursive=False)
        if len(cells) <= max(columns.values()):
            return None
        values = {key: cells[index].get_text(" ", strip=True)
                  for key, index in columns.items()}
        if not values["course_id"] or not values["course_name"]:
            return None
        text = values["remain"].strip()
        if text in ("", "-", "未知", "暂未公布"):
            remain: int | None = None
        elif _INTEGER.fullmatch(text):
            remain = int(text)
        else:
            remain = None
        return CourseItem(course_id=values["course_id"],
                          course_name=values["course_name"],
                          teacher=values["teacher"], remain=remain,
                          class_id=values.get("class_id") or None)

    for match in _ROW.finditer(raw):
        data = match.group(0)
        if b"<td" not in data.lower():
            continue
        saw_table_row = True
        if patterns and not any(pattern.search(data) for pattern in patterns):
            if len(sample_other_rows) < 8:
                sample_other_rows.append(data)
            continue
        item = parse_row(data)
        if item is None:
            continue
        examined_valid = True
        # A byte prefilter may match a teacher or course name, so always confirm
        # the actual *course_id* from its designated table cell.
        if targets and item.course_id not in targets:
            continue
        rows.append(item)

    if not examined_valid and sample_other_rows:
        examined_valid = any(parse_row(sample) is not None for sample in sample_other_rows)
    if not examined_valid:
        empty = config.get("empty_marker")
        if (isinstance(empty, str) and empty and len(empty) < 160
                and empty.encode(codec) in raw and saw_table_row is False):
            # Explicitly inspected empty-state marker is the only way to
            # distinguish "no courses" from an unexpected HTML document.
            return []
        raise ProtocolError("未能核实课程表结构；不能把未知页面当作没有余量")
    return rows


class KingoClient:
    def __init__(self, config: dict, *, offline_test_mode: bool = False):
        self.config = config
        self.p = config["protocol"]
        if self.p.get("verified") is not True:
            raise ProtocolError("protocol.verified=false：尚未核实本人课程查询接口")
        self.base = config["base_url"].rstrip("/") + "/"
        parts = urlsplit(self.base)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ProtocolError("必须使用学校官方 HTTPS 域名")
        self.origin = (parts.scheme, parts.netloc)
        # Only explicitly opted-in offline MockTransport tests can skip quotas.
        self._offline_test_mode = offline_test_mode
        self.budget = None if offline_test_mode else build_verified_budget(config, self.origin)
        self.http = httpx.AsyncClient(http1=True, http2=False, follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2,
                                keepalive_expiry=60),
            headers={"User-Agent": "KingoReadOnly/3.0", "Accept": "text/html,*/*"})
        cookie = os.environ.get("KINGO_COOKIE") or config.get("auth", {}).get("cookie", "")
        for part in cookie.split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                if name:
                    self.http.cookies.set(name, value, domain=parts.hostname, path="/")
        self.student = ""
        self.last_ms = 0.0
        self.parse_ms = 0.0
        self.stop = asyncio.Event()
        self.lock = asyncio.Lock()
        self._single_flight = SingleFlight()
        self.entry_url: str | None = None
        self.entry_loaded_at = 0.0
        self._compiled_query: dict[str, tuple[str, bytes | None]] = {}

    def url(self, path: str) -> str:
        url = urljoin(self.base, path)
        target = urlsplit(url)
        if (target.scheme, target.netloc) != self.origin or target.username or target.password:
            raise ProtocolError("禁止跨源跳转或请求")
        return url

    def prepare_query(self, term: str) -> tuple[str, bytes | None]:
        if term not in self._compiled_query:
            # [TODO: 抓包核对] F12 -> Network -> course list -> Payload:
            # compare list_fields and term_field with a real *read-only* search.
            fields = dict(self.p.get("list_fields", {}))
            fields[self.p["term_field"]] = term
            body = encode_gbk_form(list(fields.items()))
            method = self.p["list_method"].upper()
            if method == "GET":
                sep = "&" if "?" in self.p["list"] else "?"
                self._compiled_query[term] = (self.url(self.p["list"] + sep + body.decode("ascii")), None)
            elif method == "POST":
                self._compiled_query[term] = (self.url(self.p["list"]), body)
            else:
                raise ProtocolError("不支持的只读查询方法")
        return self._compiled_query[term]

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if value is None:
            return None
        value = value.strip()
        if value.isascii() and value.isdigit():
            return float(int(value))
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return None
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    async def _budgeted_request(self, method: str, url: str, headers: dict,
                                body: bytes | None) -> httpx.Response:
        if self._offline_test_mode:
            if not isinstance(self.http._transport, httpx.MockTransport):
                raise ProtocolError("离线测试模式只允许 httpx.MockTransport，不可访问真实网络")
            return await self.http.request(method, url, headers=headers, content=body)
        async with self.budget.request():
            response = await self.http.request(method, url, headers=headers, content=body)
            if response.status_code in (429, 503):
                seconds = self._retry_after_seconds(response.headers.get("Retry-After"))
                if response.status_code == 429 and seconds is None:
                    seconds = 60.0  # Local protective cooldown, not a server policy.
                if seconds is not None:
                    await self.budget.impose_retry_after(seconds)
            return response

    async def read(self, method: str, path: str, referer: str,
                   body: bytes | None = None) -> httpx.Response:
        """A bounded retry policy for GET or verified *read-only* POST only."""
        if method.upper() not in ("GET", "POST"):
            raise ProtocolError("只读客户端仅支持 GET 或经过核实的只读 POST")
        headers = {"Referer": self.url(referer)}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=GBK"
        for attempt in range(3):
            started = time.perf_counter()
            try:
                response = await self._budgeted_request(method, self.url(path), headers, body)
                if response.status_code in (401, 403, 429):
                    if response.status_code == 401:
                        raise AuthenticationError("会话无效")
                    raise RateLimitError("访问被拒绝或受到频率限制")
                if 300 <= response.status_code < 400:
                    raise AuthenticationError("学校要求重新登录或跳转")
                if response.status_code in (502, 503, 504) and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if attempt >= 2:
                    raise ProtocolError("查询连接超时") from error
                await asyncio.sleep(2 ** (attempt + 1))
            except httpx.HTTPError as error:
                raise ProtocolError("学校只读查询失败") from error
            finally:
                self.last_ms = (time.perf_counter() - started) * 1000
        raise ProtocolError("查询尝试已结束")

    async def identity(self) -> str:
        # [TODO: 抓包核对] The identity selector must return the authenticated
        # user's name in a protected official page, not merely a login label.
        r = await self.read("GET", self.p["identity_path"], self.p["identity_referer"])
        text = decode_html(r)
        if _LOGIN.search(text):
            raise AuthenticationError("网页要求重新登录")
        element = BeautifulSoup(text, "lxml").select_one(self.p["identity_selector"])
        if element is None or not element.get_text(strip=True):
            raise AuthenticationError("未能验证当前本人身份")
        self.student = element.get_text(" ", strip=True)[:40]
        return self.student

    async def warm_connection(self) -> None:
        """One normal identity GET; pool reuse is best effort, not 0-RTT."""
        await self.identity()

    async def fetch_course_list(self, term: str, course_ids: list[str] | None = None) -> list[CourseItem]:
        # Only in-flight, identical reads share work; no stale response cache.
        key = (term, None if course_ids is None else tuple(sorted(course_ids)))
        result = await self._single_flight.run(
            key, lambda: self._fetch_course_list_serial(term, course_ids))
        return list(result)

    async def _fetch_course_list_serial(self, term: str, course_ids: list[str] | None = None) -> list[CourseItem]:
        async with self.lock:
            now = time.monotonic()
            if self.entry_url is None or now - self.entry_loaded_at > 1800:
                # [TODO: 抓包核对] F12 -> Network -> Doc, enter official elective
                # page; use actual entry URL and Referer, not guessed /wsxk/*.aspx.
                r = await self.read("GET", self.p["entry"], self.p["entry_referer"])
                text = decode_html(r)
                if _LOGIN.search(text):
                    raise AuthenticationError("登录已失效")
                self.entry_url = str(r.url)
                self.entry_loaded_at = now
            path, body = self.prepare_query(term)
            r = await self.read(self.p["list_method"], path, self.entry_url, body)
            html = decode_html(r)
            if _LOGIN.search(html):
                raise AuthenticationError("登录已失效")
            if _ALERT.search(html) and ("非法访问" in html or "验证码" in html):
                raise ProtocolError("学校页面要求验证或拒绝访问")
            t0 = time.perf_counter()
            result = parse_courses(r.content, self.p, course_ids,
                                   content_type=r.headers.get("content-type", ""))
            self.parse_ms = (time.perf_counter() - t0) * 1000
            return result

    async def aclose(self) -> None:
        self.stop.set()
        await self.http.aclose()
