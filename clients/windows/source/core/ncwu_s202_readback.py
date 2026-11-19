"""Read-only, first-party S202 selected-course result-page inspection.

The 2026-11-16 firsthand interface inventory identifies this GET page, but
does not document its actual result-row schema.  A returned page is therefore
NOT proof that a particular course was selected.  Candidate rows are emitted
only for explicitly labelled course-code + class-number columns; they remain
unverified until compared with the official authenticated UI.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping

import httpx
from bs4 import BeautifulSoup

from core.ncwu_live_elective import (
    _LOGIN_MARKERS,
    MAX_BODY,
    ORIGIN,
    REFERER,
    SELECTED_PAGE,
    _decode_html,
    _validate_origin,
)
from core.ncwu_read_support import (
    classify_read_response,
    default_read_client,
    get_optional_verified_budget,
    set_scoped_cookie,
)
from core.request_budget import build_verified_budget

_LOCK = threading.Lock()
_NEXT_AT = 0.0
_CODE_HEADERS = frozenset(("课程代码", "课程编号", "课程号"))
_CLASS_HEADERS = frozenset(("教学班号", "上课班号", "上课班级编号"))
_NAME_HEADERS = frozenset(("课程名称", "课程名"))
_TEACHER_HEADERS = frozenset(("任课教师", "教师", "授课教师"))


@dataclass(frozen=True)
class CandidateSelectedCourse:
    course_code: str
    class_number: str
    course_name: str = ""
    teacher: str = ""


@dataclass(frozen=True)
class S202ReadbackResult:
    code: str
    table_count: int = 0
    recognized_tables: int = 0
    candidates: tuple[CandidateSelectedCourse, ...] = ()

    @property
    def message(self) -> str:
        if self.code == "page_observed":
            return (
                f"已读取官网 S202 已选结果页面；发现 {self.table_count} 张表，"
                f"{self.recognized_tables} 张含明确课程/班级字段，"
                f"提取 {len(self.candidates)} 条候选记录。"
                "字段未经现场逐行验收，不能据此认定最终选课成功。"
            )
        return {
            "missing_session": "请先在 EXE 内登录本人教务账号",
            "invalid_contract": "学校地址或会话范围无效，未查询",
            "busy": "已有一项 S202 已选结果读取正在执行",
            "cooldown": "已选结果检查间隔至少 60 秒，请勿反复查询",
            "policy_unverified": "请求预算配置不完整；未访问学校",
            "expired": "学校提示登录失效；不使用旧会话继续查询",
            "restricted": "学校拒绝访问或限制请求；已停止",
            "unrecognized": "已访问结果页，但页面内容不符合已知结构；不能推断选课记录",
            "unavailable": "结果页面读取失败或超时；没有发起选课操作",
        }[self.code]


def _find_header_index(values: list[str], options: frozenset[str]) -> int | None:
    matches = [i for i, value in enumerate(values) if value in options]
    return matches[0] if len(matches) == 1 else None


def parse_s202_readback(html: str) -> S202ReadbackResult:
    """Conservative in-memory result inspection, never a success receipt."""
    if not isinstance(html, str) or len(html) > MAX_BODY:
        return S202ReadbackResult("unrecognized")
    if any(m in html for m in _LOGIN_MARKERS):
        return S202ReadbackResult("expired")
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("html") is None and soup.find("body") is None:
        return S202ReadbackResult("unrecognized")
    # The NCWU global filter can return HTTP 200 even on invalid sessions.
    # A different sign-in page is likewise not evidence of enrollment data.
    if soup.find("input", attrs={"type": "password"}) is not None:
        return S202ReadbackResult("expired")
    visible = soup.get_text(" ", strip=True).replace(" ", "")
    if not any(marker in visible for marker in ("选课结果", "已选课程", "已选课")):
        return S202ReadbackResult("unrecognized")
    tables = soup.find_all("table")
    if len(tables) > 30:
        return S202ReadbackResult("unrecognized")
    records: list[CandidateSelectedCourse] = []
    recognized = 0
    for table in tables:
        headers = table.find_all("th")
        if not headers or len(headers) > 40:
            continue
        names = [h.get_text(" ", strip=True).replace(" ", "") for h in headers]
        code_col = _find_header_index(names, _CODE_HEADERS)
        class_col = _find_header_index(names, _CLASS_HEADERS)
        if code_col is None or class_col is None or code_col == class_col:
            continue
        name_col = _find_header_index(names, _NAME_HEADERS)
        teacher_col = _find_header_index(names, _TEACHER_HEADERS)
        recognized += 1
        for tr in table.find_all("tr"):
            if tr.find("th") is not None:
                continue
            cells = tr.find_all("td", recursive=False)
            if len(cells) != len(headers):
                continue
            values = [c.get_text(" ", strip=True)[:180] for c in cells]
            code, klass = values[code_col], values[class_col]
            if not code or not klass:
                continue
            records.append(CandidateSelectedCourse(
                course_code=code, class_number=klass,
                course_name=values[name_col] if name_col is not None else "",
                teacher=values[teacher_col] if teacher_col is not None else "",
            ))
            if len(records) >= 200:
                break
        if len(records) >= 200:
            break
    return S202ReadbackResult(
        "page_observed", len(tables), recognized, tuple(records))


async def read_s202_selected_results(
    config: Mapping, *, session_cookie: str | None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
) -> S202ReadbackResult:
    """Read S202 results with the current EXE session and one GET. No writes."""
    global _NEXT_AT
    if not _validate_origin(config, session_cookie):
        return S202ReadbackResult(
            "missing_session" if not session_cookie else "invalid_contract")
    if not _LOCK.acquire(blocking=False):
        return S202ReadbackResult("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_AT:
                return S202ReadbackResult("cooldown")
            _NEXT_AT = time.monotonic() + 60.0
        try:
            budget = get_optional_verified_budget(config, budget_factory)
        except Exception:
            return S202ReadbackResult("policy_unverified")
        if http_factory is None:
            http_factory = default_read_client
        async with http_factory() as client:
            set_scoped_cookie(client, session_cookie)
            if budget is not None:
                async with budget.request():
                    response = await client.get(
                        ORIGIN + SELECTED_PAGE,
                        headers={"Referer": REFERER, "Accept": "text/html"},
                        follow_redirects=False)
            else:
                response = await client.get(
                    ORIGIN + SELECTED_PAGE,
                    headers={"Referer": REFERER, "Accept": "text/html"},
                    follow_redirects=False)
            error = await classify_read_response(
                response, budget, max_bytes=MAX_BODY, require_content=True)
            if error:
                return S202ReadbackResult(error)
            try:
                html = _decode_html(response)
            except (ValueError, UnicodeError, LookupError):
                return S202ReadbackResult("unrecognized")
            return parse_s202_readback(html)
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError, KeyError):
        return S202ReadbackResult("unavailable")
    except Exception:
        return S202ReadbackResult("unavailable")
    finally:
        _LOCK.release()
