"""User-triggered NCWU S202 real HTTP course-list reading.

Fixed first-party routes from the owner's 2026-11-16 site inspection.  The
query form is read fresh from the official S202 page in the EXE's own session;
no copied student IDs, stale tokens, bid values, write actions, or browser
cookies. A single action performs up to three sequential, bounded requests.
This is NOT a validated remaining-seat or enrollment-submit adapter.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import re
import threading
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from core.client import encode_gbk_form, html_encoding
from core.latency import LatencyRecorder, LatencySample
from core.ncwu_elective_table import EXPECTED_HEADINGS
from core.request_budget import build_verified_budget
from core.ncwu_read_support import (
    classify_read_response, default_read_client, get_optional_verified_budget,
    set_scoped_cookie,
)

HOST = "jwmis.ncwu.edu.cn"
ORIGIN = "https://" + HOST
REFERER = ORIGIN + "/hsjw/frame/homes.action"
S202_PAGE = "/hsjw/student/wsxk.zx.html"
WINDOW_PATH = "/hsjw/jw/common/getWsxkTimeRange.action"
CATALOG_PATH = "/hsjw/taglib/DataTable.jsp?tableId=2568&fre=1"
SELECTED_PAGE = "/hsjw/student/wsxk.zxjg.jsp"
MAX_BODY = 2 * 1024 * 1024
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")
_LOGIN_MARKERS = ("凭证已失效", "登录已失效", "请先登录", "凭证无效")
_REQUIRED_HEADERS = {"head_kc", "head_skbh", "head_xk_status", "head_kcdm"}
_MANUAL_LOCK = threading.Lock()
_NEXT_MANUAL_AT = 0.0


class _ReadResponseUnavailable(RuntimeError):
    """HTTP transport/server rejection, not an official form-shape change."""


@dataclass(frozen=True)
class LiveElectiveCourse:
    course_name: str
    course_code: str
    class_number: str
    teacher: str
    selection_status: str
    points_label: str


@dataclass(frozen=True)
class LiveElectiveSnapshot:
    code: str
    stage: str = ""
    window: str = "unknown"
    courses: tuple[LiveElectiveCourse, ...] = ()
    recognized_headers: int = 0
    # Confirmed only by the current-session window's positive business
    # response, and checked against the current S202 ActionForm.
    academic_year: str = ""
    academic_term: str = ""

    @property
    def message(self) -> str:
        if self.code == "catalog_read":
            state = {
                "closed": "选课时间窗：未开放",
                "reported_open": "选课时间窗：接口返回 200（需官网确认轮次）",
                "unknown": "选课时间窗：未知",
            }[self.window]
            return (
                f"EXE 已通过官网接口读取 {len(self.courses)} 条课程数据；"
                f"{state}；不代表已核验剩余名额或选课成功。"
            )
        return {
            "missing_session": "请先在 EXE 内登录本人教务账号",
            "invalid_contract": "目标主机、会话或页面协议无效，未查询",
            "cooldown": "刚进行过实时查询，请至少间隔 60 秒再手动查询",
            "busy": "另一次官网实时查询尚未结束",
            "expired": "学校返回登录失效/跳转；已停止，不使用旧会话",
            "restricted": "学校限制或拒绝访问，立即停止，不重试",
            "form_missing": "官网选课页未找到 ActionForm；不能凭空构造查询表单",
            "form_incomplete": "官网当前表单或轮次信息不完整；未发送列表查询",
            "window_closed": "官网选课时间窗未开放，当前页面未生成有效查询表单；未发送列表查询",
            "table_unrecognized": "官网返回内容与当前选课表结构不一致；没有伪造课程",
            "protocol_changed": "学校页面/响应协议变化，已停止当前读取",
            "unavailable": "官网请求失败或超时；未发起选课提交",
            "policy_unverified": "已启用的服务端请求预算配置不完整；未执行查询",
        }[self.code]


def _validate_origin(config: Mapping, cookie: str | None) -> bool:
    if not isinstance(config, Mapping):
        return False
    try:
        url = urlsplit(config.get("base_url"))
        if (url.scheme != "https" or url.netloc != HOST
                or url.path not in ("", "/") or url.query or url.fragment
                or url.username is not None or url.password is not None):
            return False
    except (TypeError, ValueError):
        return False
    if not isinstance(cookie, str) or len(cookie) > 4096 or not cookie:
        return False
    seen = set()
    for pair in cookie.split(";"):
        if "=" not in pair:
            return False
        name, value = pair.strip().split("=", 1)
        if (not _COOKIE_NAME.fullmatch(name) or name in seen or not value
                or len(value) > 2048 or any(c.isspace() for c in value)):
            return False
        seen.add(name)
    return "JSESSIONID" in seen


def _decode_html(response: httpx.Response) -> str:
    raw = response.content
    if not raw or len(raw) > MAX_BODY:
        raise ValueError("invalid html size")
    codec = html_encoding(raw, response.headers.get("content-type", ""))
    return raw.decode(codec, "strict")


def _extract_query_form(page: str, *, allow_incomplete: bool = False) -> dict[str, str]:
    soup = BeautifulSoup(page, "html.parser")
    forms = [
        x for x in soup.find_all("form")
        if x.get("id") == "ActionForm" or x.get("name") == "ActionForm"
    ]
    if len(forms) != 1:
        raise LookupError("missing unique ActionForm")
    form = forms[0]
    if form.get("method", "post").lower() not in ("post", ""):
        raise ValueError("unexpected form method")
    values: dict[str, str] = {}
    for field in form.find_all(["input", "select", "textarea"]):
        name = field.get("name")
        if not name or not _FIELD_NAME.fullmatch(name):
            continue
        if name in values:
            raise ValueError("ambiguous query field")
        if field.name == "input":
            kind = field.get("type", "text").lower()
            if kind in ("submit", "button", "reset", "image", "file", "password"):
                continue
            if kind in ("checkbox", "radio") and not field.has_attr("checked"):
                continue
            value = field.get("value", "")
        elif field.name == "select":
            option = field.find("option", selected=True) or field.find("option")
            value = (option.get("value", option.get_text("", strip=True))
                     if option else "")
        else:
            value = field.get_text()
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError("invalid query value")
        values[name] = value
    if len(values) > 128:
        raise ValueError("too many fields")
    # Do not fill blanks with a captured other session's internal student ID
    # or round ID. Query only a fresh, self-contained official form.
    if (values.get("xktype") != "2"
            or (not allow_incomplete and (
                not values.get("xn") or not values.get("xq")))):
        raise ValueError("missing current-round context")
    return values


def _cell_value(cell) -> str:
    value = cell.get_text(" ", strip=True)
    if not value:
        field = cell.find("input", value=True)
        if field is not None:
            value = field.get("value", "")
    return value.strip()[:180]


def _parse_catalog(page: str) -> tuple[tuple[LiveElectiveCourse, ...], int]:
    """Map current 2568 columns by first-party IDs, not stale column offsets.

    NCWU DataTable pages may use either positional `<td>` cells or
    `<td id="trN_kcdm">` fields.  Never derive class identity or seats from
    unlabelled/unknown columns.
    """
    soup = BeautifulSoup(page, "html.parser")
    known = set(EXPECTED_HEADINGS)
    matches = []
    for table in soup.find_all("table"):
        ids = {x.get("id") for x in table.find_all(attrs={"id": True})
               if x.get("id") in known}
        if _REQUIRED_HEADERS.issubset(ids):
            matches.append((table, ids))
    if len(matches) != 1:
        raise ValueError("missing or ambiguous catalog table")
    table, ids = matches[0]
    if ids != known:
        raise ValueError("incomplete catalog columns")
    header_orders = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        keys = [c.get("id") for c in cells if c.get("id") in known]
        if keys:
            if len(keys) != len(known) or len(set(keys)) != len(known):
                raise ValueError("incomplete or duplicated header")
            header_orders.append(tuple(keys))
    if len(header_orders) != 1:
        raise ValueError("ambiguous header row")
    ordered_fields = tuple(k.removeprefix("head_") for k in header_orders[0])
    recognized_fields = set(ordered_fields)
    courses: list[LiveElectiveCourse] = []
    for tr in table.find_all("tr"):
        if any(node.get("id") in known for node in tr.find_all(attrs={"id": True})):
            continue
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        by_name: dict[str, str] = {}
        row_id = tr.get("id", "")
        prefix = row_id + "_" if re.fullmatch(r"tr[0-9]+", row_id) else ""
        for cell in cells:
            key = cell.get("name") or ""
            cid = cell.get("id", "")
            if not key and prefix and cid.startswith(prefix):
                key = cid[len(prefix):]
            if key in recognized_fields:
                if key in by_name:
                    raise ValueError("ambiguous duplicate cell name")
                by_name[key] = _cell_value(cell)
        if all(k in by_name for k in ("kcdm", "skbh", "kc")):
            get = lambda key: by_name.get(key, "")
        elif not by_name and len(cells) == len(ordered_fields):
            by_position = dict(zip(ordered_fields, (_cell_value(c) for c in cells)))
            get = lambda key: by_position.get(key, "")
        else:
            continue  # Continuation or incomplete row is not a course.
        code = get("kcdm")
        class_number = get("skbh")
        if not code or not class_number:
            continue
        courses.append(LiveElectiveCourse(
            course_name=get("kc"), course_code=code,
            class_number=class_number, teacher=get("rkjs"),
            selection_status=get("xk_status"),
            points_label=get("xk_points"),
        ))
        if len(courses) >= 500:
            break
    return tuple(courses), len(ids)


def _window_state(response: httpx.Response) -> str:
    if len(response.content) > 16384:
        return "unknown"
    try:
        info = json.loads(response.content.decode("utf-8-sig"))
        status = str(info.get("status", ""))
        if status == "400" and "选课时间" in str(info.get("message", "")):
            return "closed"
        if status == "200":
            return "reported_open"
    except (ValueError, UnicodeError, AttributeError):
        pass
    return "unknown"


def _window_runtime_fields(response: httpx.Response) -> dict[str, str] | None:
    """Read the same-session data used by the first-party initWsxkTimeRange().

    The school's HTML includes empty hidden xn/xq/lcid fields. Its own JS
    fills these *only when* the business response reports status 200.
    Never hydrate a form using a closed/unknown window or archived values.
    """
    if _window_state(response) != "reported_open":
        return None
    try:
        record = json.loads(response.content.decode("utf-8-sig"))
        raw = record.get("result")
        fields = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(fields, dict) or str(fields.get("lx", "2")) != "2":
            return None
        required = ("xh", "xn", "xqM", "lcid", "nj", "zydm")
        for key in required:
            value = fields.get(key)
            if (not isinstance(value, str) or not value.strip()
                    or len(value) > 512):
                return None
        return fields
    except (ValueError, TypeError, UnicodeError, AttributeError):
        return None


def _hydrate_round_form(form: dict[str, str], runtime: Mapping) -> dict[str, str]:
    """Apply only fresh values the first-party page itself maps to inputs."""
    mapping = {
        "xh": "xh", "xn": "xn", "xq": "xqM", "lcid": "lcid",
        "nj": "nj", "zydm": "zydm", "yxbdm": "yxbdm",
        "is_xjls": "isXjls", "sfbd": "sfbd", "djs": "djs",
        "xfsx": "xfsx", "mssx": "mssx", "kxkc": "kxkc",
        "xxkckzfs": "xxkckzfs", "yxkzyfxxk": "yxkzyfxxk",
        "yxsjct": "yxsjct", "_kcfw": "kcfw",
    }
    result = dict(form)
    for dest, source in mapping.items():
        if dest not in result or source not in runtime:
            continue
        value = runtime[source]
        if not isinstance(value, str) or len(value) > 512:
            raise ValueError("invalid current-round field")
        if result[dest] and result[dest] != value:
            raise ValueError("conflicting current-round field")
        result[dest] = value
    if any(not result.get(key) for key in ("xktype", "xh", "xn", "xq", "lcid")):
        raise ValueError("current-round form remains incomplete")
    return result


@asynccontextmanager
async def _budget_slot(budget):
    if budget is None:
        yield
    else:
        async with budget.request():
            yield


async def live_s202_snapshot(
    config: Mapping, *, session_cookie: str | None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
    latency_recorder: LatencyRecorder | None = None,
) -> LiveElectiveSnapshot:
    """One manual, bounded real HTTP snapshot, never a background loop."""
    global _NEXT_MANUAL_AT
    if not _validate_origin(config, session_cookie):
        return LiveElectiveSnapshot("missing_session" if not session_cookie else "invalid_contract")
    if not _MANUAL_LOCK.acquire(blocking=False):
        return LiveElectiveSnapshot("busy")
    try:
        if enforce_manual_cooldown and time.monotonic() < _NEXT_MANUAL_AT:
            return LiveElectiveSnapshot("cooldown")
        try:
            budget = get_optional_verified_budget(config, budget_factory)
        except Exception:
            return LiveElectiveSnapshot("policy_unverified")
        if http_factory is None:
            http_factory = default_read_client
        stage = "page"
        async with http_factory() as client:
            set_scoped_cookie(client, session_cookie)
            async def request(method: str, path: str, *, form=None):
                """Measure bounded S202 HTTP transport without recording identities."""
                global _NEXT_MANUAL_AT
                started = time.perf_counter()
                budget_started = started
                budget_ready = None
                network_started = None
                network_finished = None
                response = None
                try:
                    headers = {
                        "Referer": REFERER,
                        "Accept": "text/html,application/json,text/plain",
                    }
                    request_opts = {}
                    if form is not None:
                        # Preserve the first-party GBK form body verbatim.
                        headers["Content-Type"] = (
                            "application/x-www-form-urlencoded; charset=GBK")
                        request_opts["content"] = encode_gbk_form(list(form.items()))
                    budget_started = time.perf_counter()
                    async with _budget_slot(budget):
                        budget_ready = time.perf_counter()
                        # Only a request that is actually about to start consumes
                        # the local cooldown. Invalid policy / failed setup do not.
                        # The outer _MANUAL_LOCK serializes this decision.
                        if enforce_manual_cooldown and stage == "page":
                            _NEXT_MANUAL_AT = time.monotonic() + 60.0
                        network_started = time.perf_counter()
                        response = await client.request(
                            method, ORIGIN + path, headers=headers,
                            follow_redirects=False, **request_opts,
                        )
                        network_finished = time.perf_counter()
                    error = await classify_read_response(
                        response, budget, max_bytes=MAX_BODY)
                    if error in ("expired", "restricted"):
                        raise PermissionError(error)
                    if error:
                        # A 200 with an oversized/invalid payload breaks the
                        # expected page contract; an HTTP failure does not.
                        if response.status_code == 200:
                            raise ValueError("unexpected response shape or size")
                        raise _ReadResponseUnavailable("unavailable")
                    return response
                finally:
                    if latency_recorder is not None:
                        finished = time.perf_counter()
                        wait_end = budget_ready if budget_ready is not None else finished
                        http_end = network_finished if network_finished is not None else finished
                        try:
                            latency_recorder.record(LatencySample(
                                kind=f"s202_{stage}",
                                status=response.status_code if response is not None else None,
                                budget_wait_ms=(wait_end - budget_started) * 1000,
                                request_ms=((http_end - network_started) * 1000
                                            if network_started is not None else 0.0),
                                total_ms=(finished - started) * 1000,
                                bytes_read=(len(response.content) if response is not None else 0),
                                note="http_transport_only",
                            ))
                        except Exception:
                            # Diagnostics must never change request outcomes.
                            pass
            page_resp = await request("GET", S202_PAGE)
            page = _decode_html(page_resp)
            if any(marker in page for marker in _LOGIN_MARKERS):
                return LiveElectiveSnapshot("expired", stage)
            try:
                form = _extract_query_form(page, allow_incomplete=True)
            except LookupError:
                return LiveElectiveSnapshot("form_missing", stage)
            except ValueError:
                return LiveElectiveSnapshot("form_incomplete", stage)
            stage = "window"
            window_resp = await request("POST", WINDOW_PATH + "?xktype=2")
            window = _window_state(window_resp)
            runtime = _window_runtime_fields(window_resp)
            verified_year = verified_term = ""
            if runtime is not None:
                for local_name, server_name in (
                    ("xh", "xh"), ("xn", "xn"),
                    ("xq", "xqM"), ("lcid", "lcid"),
                ):
                    local = form.get(local_name)
                    if local and local != runtime[server_name]:
                        # A stale/mismatched form cannot be compared against
                        # a new round. It also must not be sent to DataTable.
                        return LiveElectiveSnapshot(
                            "form_incomplete", stage, window)
                verified_year = runtime["xn"]
                verified_term = runtime["xqM"]
            if not form.get("xn") or not form.get("xq") or not form.get("lcid"):
                if window == "closed":
                    return LiveElectiveSnapshot("window_closed", stage, window)
                if runtime is None:
                    return LiveElectiveSnapshot("form_incomplete", stage, window)
                try:
                    form = _hydrate_round_form(form, runtime)
                except ValueError:
                    return LiveElectiveSnapshot("form_incomplete", stage, window)
            stage = "catalog"
            table_resp = await request("POST", CATALOG_PATH, form=form)
            html = _decode_html(table_resp)
            if any(marker in html for marker in _LOGIN_MARKERS):
                return LiveElectiveSnapshot("expired", stage)
            try:
                courses, headers = _parse_catalog(html)
            except ValueError:
                return LiveElectiveSnapshot("table_unrecognized", stage, window)
            return LiveElectiveSnapshot(
                "catalog_read", stage, window, courses, headers,
                academic_year=verified_year, academic_term=verified_term)
    except PermissionError as exc:
        return LiveElectiveSnapshot(
            "restricted" if str(exc) == "restricted" else "expired")
    except (httpx.HTTPError, _ReadResponseUnavailable):
        # A server failure or network error is not evidence of a changed form.
        return LiveElectiveSnapshot("unavailable", stage)
    except (UnicodeError, ValueError, KeyError, TypeError):
        return LiveElectiveSnapshot("protocol_changed" if stage == "page" else "unavailable", stage)
    except Exception:
        return LiveElectiveSnapshot("unavailable")
    finally:
        _MANUAL_LOCK.release()
