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

from core.client import html_encoding
from core.ncwu_elective_table import EXPECTED_HEADINGS
from core.request_budget import build_verified_budget

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
    # The observed first-party table has 17 fixed columns; never reinterpret
    # 'selection status' or 'selection coins' as remaining-seat quantity.
    index = {key: n for n, key in enumerate(EXPECTED_HEADINGS)}
    courses: list[LiveElectiveCourse] = []
    for tr in table.find_all("tr"):
        if any(node.get("id") in known for node in tr.find_all(attrs={"id": True})):
            continue
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(EXPECTED_HEADINGS):
            continue
        get = lambda key: _cell_value(cells[index[key]])
        code = get("head_kcdm")
        class_number = get("head_skbh")
        if not code or not class_number:
            continue
        courses.append(LiveElectiveCourse(
            course_name=get("head_kc"), course_code=code,
            class_number=class_number, teacher=get("head_rkjs"),
            selection_status=get("head_xk_status"),
            points_label=get("head_xk_points"),
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
) -> LiveElectiveSnapshot:
    """One manual, bounded real HTTP snapshot, never a background loop."""
    global _NEXT_MANUAL_AT
    if not _validate_origin(config, session_cookie):
        return LiveElectiveSnapshot("missing_session" if not session_cookie else "invalid_contract")
    if not _MANUAL_LOCK.acquire(blocking=False):
        return LiveElectiveSnapshot("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_MANUAL_AT:
                return LiveElectiveSnapshot("cooldown")
            _NEXT_MANUAL_AT = time.monotonic() + 60.0
        policy = config.get("server_policy", {})
        budget = None
        if isinstance(policy, Mapping) and policy.get("verified") is True:
            try:
                budget = budget_factory(dict(config), ("https", HOST))
            except Exception:
                return LiveElectiveSnapshot("policy_unverified")
        if http_factory is None:
            http_factory = lambda: httpx.AsyncClient(
                follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        stage = "page"
        async with http_factory() as client:
            for item in session_cookie.split(";"):
                name, value = item.strip().split("=", 1)
                client.cookies.set(name, value, domain=HOST, path="/hsjw")
            async def request(method: str, path: str, *, form=None):
                async with _budget_slot(budget):
                    response = await client.request(
                        method, ORIGIN + path, data=form,
                        headers={"Referer": REFERER,
                                 "Accept": "text/html,application/json,text/plain"},
                        follow_redirects=False,
                    )
                if response.status_code in (401, 302, 303, 307, 308):
                    raise PermissionError("expired")
                if response.status_code in (403, 429):
                    if response.status_code == 429 and budget is not None:
                        from core.client import KingoClient
                        seconds = KingoClient._retry_after_seconds(
                            response.headers.get("Retry-After"))
                        await budget.impose_retry_after(seconds or 60.0)
                    raise PermissionError("restricted")
                if response.status_code != 200 or len(response.content) > MAX_BODY:
                    raise ValueError("unexpected response")
                return response
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
            if not form.get("xn") or not form.get("xq") or not form.get("lcid"):
                if window == "closed":
                    return LiveElectiveSnapshot("window_closed", stage, window)
                runtime = _window_runtime_fields(window_resp)
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
                "catalog_read", stage, window, courses, headers)
    except PermissionError as exc:
        return LiveElectiveSnapshot(
            "restricted" if str(exc) == "restricted" else "expired")
    except (httpx.HTTPError, UnicodeError, ValueError, KeyError, TypeError):
        return LiveElectiveSnapshot("protocol_changed" if stage == "page" else "unavailable", stage)
    except Exception:
        return LiveElectiveSnapshot("unavailable")
    finally:
        _MANUAL_LOCK.release()
