"""Single, first-party NCWU course-plan query for pre-opening preparation.

Based on the user's own November 2026 code in 07xh/kingo-live/ncwu/
hs_kcbcx.py, not on a guessed endpoint. This reads tableId=5327042, a
course-plan *snapshot*, not S202 selection table 2568 or a guaranteed live
vacancy. Form identity/term are obtained from the same EXE session and page.
Never writes a registration or silently fabricates qdrs from cap - enrolled.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Callable, Mapping

import httpx
from bs4 import BeautifulSoup

from core.client import encode_gbk_form
from core.ncwu_live_elective import (
    HOST, ORIGIN, _decode_html, _LOGIN_MARKERS, _validate_origin, MAX_BODY,
)
from core.ncwu_s202_score import _context_from_window
from core.ncwu_live_elective import WINDOW_PATH
from core.request_budget import build_verified_budget

PAGE = "/hsjw/student/wsxk.kcbcx10319.html?menucode=S2020103"
TABLE = "/hsjw/taglib/DataTable.jsp?tableId=5327042&clientWidth=1210"
MENU = "S2020103"
_LOCK = threading.Lock()
_NEXT_AT = 0.0
_SAFE = re.compile(r"^[A-Za-z0-9_|.-]{1,80}$")
_COURSE = re.compile(r"^\[([A-Za-z0-9]+)\]\s*(.+)$")
_TR_ID = re.compile(r"^tr[0-9]{1,5}$")
_TOTAL = re.compile(
    r"showTotalRecord\(\s*['\"]5327042['\"]\s*,\s*['\"]([0-9]{1,6})['\"]\s*\)"
)


@dataclass(frozen=True)
class CoursePlanRow:
    code: str
    name: str
    class_id: str
    class_name: str
    teacher: str
    time: str
    room: str
    capacity: int | None
    enrolled: int | None
    plan_available: int | None


@dataclass(frozen=True)
class PlanSnapshot:
    code: str
    rows: tuple[CoursePlanRow, ...] = ()
    record_count: int | None = None
    stage: str = ""
    # Non-sensitive term metadata from the current first-party window.
    # Optional to preserve existing callers and synthetic unit fixtures.
    academic_year: str = ""
    academic_term: str = ""

    @property
    def message(self) -> str:
        if self.code == "read":
            return (
                f"官网开课计划：解析 {len(self.rows)} 个课程教学班；"
                f"表格原始记录 {self.record_count}。"
                "可选人数为开课计划查询时的服务器快照，"
                "不是正式选课窗口的实时余量或选中凭证。"
            )
        return {
            "missing_session": "请先在 EXE 登录本人教务账号",
            "invalid_contract": "官网来源或当前会话无效，未查询",
            "busy": "已有一项开课计划读取任务进行中",
            "cooldown": "开课计划查询需至少间隔 60 秒",
            "policy_unverified": "已核验的访问预算不可用，未查询",
            "expired": "学校提示会话失效，需重新登录",
            "restricted": "学校拒绝或限制本次查询，未重试",
            "round_unavailable": "官网当前轮次未提供完整身份/学期字段，未查询开课计划",
            "form_unavailable": "开课计划页面未给出可验证的本人查询条件，未发送表格 POST",
            "plan_unrecognized": "开课计划返回的表格结构不匹配，未编造课程和余量",
            "unavailable": "学校查询失败或超时，没有发送选课提交",
        }[self.code]


def _scoped_fields_from_page(html: str, scope: Mapping[str, str]) -> list[tuple[str, str]] | None:
    soup = BeautifulSoup(html, "html.parser")
    forms = [
        form for form in soup.find_all("form")
        if form.get("id") == "ActionForm" or form.get("name") == "ActionForm"
    ]
    if len(forms) != 1:
        return None
    form = forms[0]
    if str(form.get("method", "post")).lower() not in ("", "post"):
        return None
    fields: dict[str, str] = {}
    allowed = {
        "xh", "xn", "xq", "nj", "pycc", "dwh", "zydm", "yxbdm",
        "sel_pycc", "sel_nj", "sel_yxb", "sel_zydm",
    }
    for elm in form.find_all(["input", "select"]):
        key = elm.get("name")
        if key not in allowed:
            continue
        if key in fields:
            return None
        if elm.name == "select":
            selected = elm.find("option", selected=True) or elm.find("option")
            value = selected.get("value", selected.get_text(strip=True)) if selected else ""
        else:
            value = elm.get("value", "")
        if not isinstance(value, str) or len(value) > 80:
            return None
        if value:
            fields[key] = value

    # NCWU represents the first semester as 0. Check the actual form against
    # the same-session round API; never reuse last term's settings.
    for form_key, scope_key in (("xh", "xh"), ("xn", "xn"), ("xq", "xqM")):
        observed = fields.get(form_key)
        expected = scope.get(scope_key)
        if not isinstance(expected, str) or not expected or (
            observed and observed != expected
        ):
            return None
    for form_key, scope_key in (("nj", "nj"), ("zydm", "zydm"),
                                ("dwh", "yxbdm"), ("yxbdm", "yxbdm")):
        observed, expected = fields.get(form_key), scope.get(scope_key)
        if observed and expected and observed != expected:
            return None

    xn = scope["xn"]
    xq = scope["xqM"]
    nj = fields.get("nj") or scope.get("nj")
    zydm = fields.get("zydm") or scope.get("zydm")
    yxb = fields.get("dwh") or fields.get("yxbdm") or scope.get("yxbdm")
    pycc = fields.get("pycc") or fields.get("sel_pycc")
    if not all((nj, zydm, yxb, pycc)):
        return None
    if not all(_SAFE.fullmatch(x) for x in (xn, xq, nj, zydm, yxb, pycc)):
        return None
    # Construct only the known 5327042 read-only filter contract, with
    # account/term/major/college values from this actual current session.
    return [
        ("initQry", "0"), ("xktype", "2"),
        ("xn", xn), ("xq", xq), ("nj", nj), ("pycc", pycc),
        ("dwh", yxb), ("zydm", zydm),
        ("kclb1", ""), ("kclb2", ""), ("isbyk", ""), ("items", ""),
        ("xnxq", f"{xn},{xq}"),
        ("btnFilter", "类别过滤"), ("btnSubmit", "提交"),
        ("sel_pycc", pycc), ("sel_nj", nj),
        ("sel_yxb", yxb), ("sel_zydm", zydm),
        ("kcmk", ""), ("sel_schoolarea", ""),
        ("sel_kclb1", ""), ("sel_kclb2", ""),
        ("sel_kc", ""), ("sel_rkjs", ""),
        ("sel_cddwdm", ""), ("menucode_current", MENU),
    ]


def _int_strict(s: str) -> int | None:
    if not s or not s.isascii() or not s.isdecimal():
        return None
    n = int(s)
    return n if n <= 1000000 else None


def parse_course_plan(html: str) -> tuple[tuple[CoursePlanRow, ...], int]:
    """Parse historical first-party O/E rows and merge continuation times."""
    if not isinstance(html, str) or len(html) > MAX_BODY:
        raise ValueError("bad plan size")
    m = _TOTAL.search(html)
    if m is None:
        raise ValueError("no 5327042 total marker")
    total = int(m.group(1))
    if total > 10000:
        raise ValueError("implausible total")
    soup = BeautifulSoup(html, "html.parser")
    parsed: list[dict] = []
    for tr in soup.find_all("tr"):
        rid = tr.get("id", "")
        if not _TR_ID.fullmatch(rid):
            continue
        if tr.get("class", []) and not set(tr.get("class", [])).intersection({"O", "E"}):
            continue
        row: dict[str, str] = {}
        for td in tr.find_all("td", recursive=False):
            cid = td.get("id", "")
            if cid.startswith(rid + "_"):
                key = cid[len(rid) + 1:]
                if key in row:
                    raise ValueError("duplicate plan key")
                row[key] = td.get_text(" ", strip=True)[:180]
        if not row:
            continue
        course = _COURSE.fullmatch(row.get("kc", ""))
        if course is None:
            if parsed:
                for key in ("sksj", "skdd"):
                    extra = row.get(key, "").strip()
                    if extra:
                        original = parsed[-1].get(key, "")
                        if extra not in original:
                            parsed[-1][key] = (original + " | " + extra).strip(" |")
            continue
        row["_code"] = course.group(1)
        row["_name"] = course.group(2)
        parsed.append(row)
        if len(parsed) > 500:
            raise ValueError("too many course rows")
    if total > 0 and not parsed:
        raise ValueError("record count but no recognizable course rows")
    if total == 0 and parsed:
        raise ValueError("contradictory record count")
    out: list[CoursePlanRow] = []
    for row in parsed:
        enrolled_raw = row.get("xkrs", "").split("/", 1)[0].strip()
        out.append(CoursePlanRow(
            code=row["_code"], name=row["_name"],
            class_id=row.get("curent_skbjdm", "") or row.get("skbjdm", ""),
            class_name=row.get("skbjmc", ""),
            teacher=row.get("rkjs", ""), time=row.get("sksj", ""),
            room=row.get("skdd", ""),
            capacity=_int_strict(row.get("xkrssx", "")),
            enrolled=_int_strict(enrolled_raw),
            plan_available=_int_strict(row.get("qdrs", "")),
        ))
    return tuple(out), total


async def read_current_course_plan(
    config: Mapping, *, session_cookie: str | None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
) -> PlanSnapshot:
    """At most three serialized official reads. No user changes or loops."""
    global _NEXT_AT
    if not _validate_origin(config, session_cookie):
        return PlanSnapshot("missing_session" if not session_cookie else "invalid_contract")
    if not _LOCK.acquire(blocking=False):
        return PlanSnapshot("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_AT:
                return PlanSnapshot("cooldown")
            _NEXT_AT = time.monotonic() + 60.0
        policy = config.get("server_policy", {})
        budget = None
        if isinstance(policy, Mapping) and policy.get("verified") is True:
            try:
                budget = budget_factory(dict(config), ("https", HOST))
            except Exception:
                return PlanSnapshot("policy_unverified")
        if http_factory is None:
            http_factory = lambda: httpx.AsyncClient(
                follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        async with http_factory() as client:
            for pair in session_cookie.split(";"):
                name, value = pair.strip().split("=", 1)
                client.cookies.set(name, value, domain=HOST, path="/hsjw")
            stage = "window"

            async def req(method: str, path: str, *, content: bytes | None = None,
                          referer: str | None = None) -> httpx.Response:
                headers = {
                    "Referer": ORIGIN + (referer or PAGE),
                    "Accept": "text/html,application/json,text/plain",
                }
                if content is not None:
                    headers["Content-Type"] = (
                        "application/x-www-form-urlencoded; charset=GBK"
                    )
                async def send():
                    return await client.request(
                        method, ORIGIN + path, content=content,
                        headers=headers, follow_redirects=False)
                if budget is not None:
                    async with budget.request():
                        resp = await send()
                else:
                    resp = await send()
                if resp.status_code in (401, 302, 303, 307, 308):
                    raise PermissionError("expired")
                if resp.status_code in (403, 429):
                    if resp.status_code == 429 and budget is not None:
                        from core.client import KingoClient
                        pause = KingoClient._retry_after_seconds(
                            resp.headers.get("Retry-After"))
                        await budget.impose_retry_after(pause or 60.0)
                    raise PermissionError("restricted")
                if resp.status_code != 200 or not resp.content or len(resp.content) > MAX_BODY:
                    raise ValueError("bad response")
                return resp
            # Even if school reports time window closed, its read-only
            # response can still provide current term/major/college context.
            window = await req("POST", WINDOW_PATH + "?xktype=2", content=b"")
            scope = _context_from_window(window.content)
            if scope is None:
                return PlanSnapshot("round_unavailable", stage="window")
            stage = "page"
            page = await req("GET", PAGE)
            html = _decode_html(page)
            if any(m in html for m in _LOGIN_MARKERS):
                return PlanSnapshot("expired", stage="page")
            form = _scoped_fields_from_page(html, {
                **scope,
                # Account/term are confirmed by _context_from_window; optional
                # other session-context fields remain only when the endpoint
                # actually provides them.
                **_optional_scope_fields(window.content),
            })
            if form is None:
                return PlanSnapshot("form_unavailable", stage="page")
            stage = "table"
            table = await req("POST", TABLE, content=encode_gbk_form(form))
            body = _decode_html(table)
            if any(m in body for m in _LOGIN_MARKERS):
                return PlanSnapshot("expired", stage="table")
            try:
                rows, total = parse_course_plan(body)
            except ValueError:
                return PlanSnapshot("plan_unrecognized", stage="table")
            return PlanSnapshot(
                "read", rows, total, "table",
                academic_year=scope["xn"], academic_term=scope["xqM"])
    except PermissionError as err:
        return PlanSnapshot("restricted" if str(err) == "restricted" else "expired")
    except (httpx.HTTPError, ValueError, KeyError, UnicodeError, TypeError):
        return PlanSnapshot("unavailable")
    except Exception:
        return PlanSnapshot("unavailable")
    finally:
        _LOCK.release()


def _optional_scope_fields(raw: bytes) -> dict[str, str]:
    """Return only same-session non-identifying lookup fields, transiently."""
    from core.ncwu_s202_score import _parse_json_record
    record = _parse_json_record(raw)
    if record is None:
        return {}
    data = record.get("result")
    try:
        import json
        obj = json.loads(data) if isinstance(data, str) else data
    except (TypeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {
        k: v for k, v in obj.items()
        if k in ("nj", "zydm", "yxbdm", "pycc")
        and isinstance(v, str) and 0 < len(v) <= 80
    }
