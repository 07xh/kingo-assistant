"""Owner-session NCWU academic schedule AJAX reader (2026-11-17 evidence).

Separate from S202 elective 2568 and the older signed WAP timetable. This
is a single read-only POST; source cells stay unverified until matched to
the school's visible timetable. No enrollment operation, retries or polling.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from core.ncwu_live_elective import _LOGIN_MARKERS, ORIGIN, REFERER, _validate_origin
from core.ncwu_read_support import (
    classify_read_response,
    default_read_client,
    get_optional_verified_budget,
    set_scoped_cookie,
)
from core.request_budget import build_verified_budget

PATH = "/hsjw/frame/desk/showLessonScheduleDetail.action"
_DAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_SECTION = re.compile(r"^(\d{1,2})\s*[（(]\s*(\d\d:\d\d)\s*[-–—]\s*(\d\d:\d\d)\s*[)）]$")
_SEMESTER = re.compile(r"^(20\d{2})-(20\d{2})-([12])$")
_LOCK = threading.Lock()
_NEXT_AT = 0.0
_MAX_BYTES = 650_000


@dataclass(frozen=True)
class LessonCell:
    weekday: int
    section: int
    raw_text: str


@dataclass(frozen=True)
class LessonDetailResult:
    code: str
    section_times: tuple[tuple[int, str, str], ...] = ()
    cells: tuple[LessonCell, ...] = ()
    # The source is an independently observed personal timetable, NOT an
    # elective catalog or verified attendance/enrollment proof.
    verified: bool = False
    trusted_sync_allowed: bool = False

    @property
    def message(self) -> str:
        if self.code == "read":
            return (f"教务课表 AJAX 已读取 {len(self.section_times)} 个节次、"
                    f"{len(self.cells)} 个非空课表单元格；尚未独立核对课程记录。"
                    "这不是可选课程、实时余量或选课成功凭证。")
        return {
            "missing_session": "请先在 EXE 内登录本人教务账号",
            "invalid_contract": "会话、学期或官网地址无效，未查询",
            "busy": "已有课表读取正在进行",
            "cooldown": "课表读取需至少间隔 60 秒",
            "policy_unverified": "学校请求预算配置不完整，未查询",
            "expired": "学校提示登录失效，请重新登录",
            "restricted": "学校拒绝或限制访问，没有重试",
            "schema_unverified": "课表 HTML 与实测 12 节次结构不匹配，没有生成课程记录",
            "unavailable": "课表接口请求失败，没有发起任何选课提交",
        }[self.code]


def _semester_parts(semester: str) -> tuple[str, str] | None:
    match = _SEMESTER.fullmatch(semester) if isinstance(semester, str) else None
    if not match or int(match[2]) != int(match[1]) + 1:
        return None
    return match[1], match[3]


def parse_lesson_detail(raw: str, semester: str) -> LessonDetailResult:
    """Inspect the official 14-row timetable; never infer missing cells."""
    parts = _semester_parts(semester)
    if (parts is None or not isinstance(raw, str) or not raw.strip()
            or len(raw) > _MAX_BYTES or any(x in raw for x in _LOGIN_MARKERS)):
        return LessonDetailResult("schema_unverified")
    soup = BeautifulSoup(raw, "html.parser")
    if soup.find("input", attrs={"type": "password"}) is not None:
        return LessonDetailResult("schema_unverified")
    matches = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        headers = []
        for i, row in enumerate(rows):
            words = [c.get_text(" ", strip=True).replace(" ", "")
                     for c in row.find_all(["th", "td"], recursive=False)]
            if all(day in words for day in _DAYS) and [
                words.index(day) for day in _DAYS
            ] == sorted(words.index(day) for day in _DAYS):
                headers.append(i)
        if len(headers) == 1:
            matches.append((rows, headers[0]))
    if len(matches) != 1:
        return LessonDetailResult("schema_unverified")
    rows, header = matches[0]
    title = rows[0].get_text(" ", strip=True).replace(" ", "")
    expected_year = parts[0] + "-" + str(int(parts[0]) + 1)
    expected_term = "第一学期" if parts[1] == "1" else "第二学期"
    if expected_year not in title or expected_term not in title:
        return LessonDetailResult("schema_unverified")
    sections = {}
    cells = []
    for row in rows[header + 1:]:
        tds = row.find_all(["th", "td"], recursive=False)
        markers = []
        for index, cell in enumerate(tds):
            text = cell.get_text(" ", strip=True)
            match = _SECTION.fullmatch(text)
            if match:
                markers.append((index, match))
        if not markers:
            continue
        if len(markers) != 1:
            return LessonDetailResult("schema_unverified")
        index, match = markers[0]
        num = int(match[1])
        if num not in range(1, 13) or num in sections or len(tds) - index - 1 != 7:
            return LessonDetailResult("schema_unverified")
        sections[num] = (num, match[2], match[3])
        for weekday, cell in enumerate(tds[index + 1:], 1):
            if cell.has_attr("rowspan") or cell.has_attr("colspan"):
                return LessonDetailResult("schema_unverified")
            text = cell.get_text(" ", strip=True)
            if len(text) > 2200:
                return LessonDetailResult("schema_unverified")
            if text:
                cells.append(LessonCell(weekday, num, text))
    if sorted(sections) != list(range(1, 13)):
        return LessonDetailResult("schema_unverified")
    return LessonDetailResult(
        "read", tuple(sections[n] for n in range(1, 13)), tuple(cells))


async def read_lesson_detail(
    config: Mapping, *, session_cookie: str | None, semester: str,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
) -> LessonDetailResult:
    """One same-origin, authorized POST of weeks=&xnxq=YYYY,termIndex."""
    global _NEXT_AT
    if not _validate_origin(config, session_cookie) or not _semester_parts(semester):
        return LessonDetailResult(
            "missing_session" if not session_cookie else "invalid_contract")
    if not _LOCK.acquire(blocking=False):
        return LessonDetailResult("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_AT:
                return LessonDetailResult("cooldown")
            _NEXT_AT = time.monotonic() + 60.0
        try:
            budget = get_optional_verified_budget(config, budget_factory)
        except Exception:
            return LessonDetailResult("policy_unverified")
        factory = http_factory or default_read_client
        year, term = _semester_parts(semester)
        form = urlencode({"weeks": "", "xnxq": year + "," + str(int(term) - 1)})
        async with factory() as client:
            set_scoped_cookie(client, session_cookie)
            headers = {
                "Referer": REFERER,
                "Accept": "text/html",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            if budget is not None:
                async with budget.request():
                    response = await client.post(
                        ORIGIN + PATH, content=form.encode("ascii"),
                        headers=headers, follow_redirects=False)
            else:
                response = await client.post(
                    ORIGIN + PATH, content=form.encode("ascii"),
                    headers=headers, follow_redirects=False)
            code = await classify_read_response(
                response, budget, max_bytes=_MAX_BYTES, require_content=True)
            if code:
                return LessonDetailResult(code)
            mime = response.headers.get("content-type", "").lower()
            if mime and not mime.startswith(("text/html", "application/xhtml+xml")):
                return LessonDetailResult("schema_unverified")
            try:
                html = response.content.decode("utf-8-sig", errors="strict")
            except UnicodeError:
                return LessonDetailResult("schema_unverified")
            if any(x in html for x in _LOGIN_MARKERS):
                return LessonDetailResult("expired")
            return parse_lesson_detail(html, semester)
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError):
        return LessonDetailResult("unavailable")
    except Exception:
        return LessonDetailResult("unavailable")
    finally:
        _LOCK.release()
