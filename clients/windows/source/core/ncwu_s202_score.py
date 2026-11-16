"""Bounded personal S202 selected-course count probe.

Observed in 07xh/kingo-live/ncwu/hs_sniper.py: the official first-party
getSelectLessonScoreKcsInfo.action accepts `xn`, `xq_m`, `xh`. The values
MUST come from the same session's live time-window response, not the user's
YAML, a HAR replay, or another account. No selected-class or enrollment
success claim is made from aggregate counts.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from typing import Callable, Mapping

import httpx

from core.client import encode_gbk_form
from core.ncwu_live_elective import HOST, ORIGIN, REFERER, WINDOW_PATH, _validate_origin
from core.request_budget import build_verified_budget

SCORE_PATH = "/hsjw/jw/common/getSelectLessonScoreKcsInfo.action"
_MAX_RESPONSE = 16 * 1024
_LIMIT = threading.Lock()
_NEXT_ALLOWED_AT = 0.0
_EXPIRED = ("凭证已失效", "登录已失效", "请先登录", "请重新登录")


@dataclass(frozen=True)
class PersonalScoreCount:
    code: str
    selected_count: int | None = None
    scope_verified: bool = False

    @property
    def message(self) -> str:
        if self.code == "count_observed":
            return (
                f"本人 S202 已选门数：{self.selected_count}（官方汇总接口返回）。"
                "门数无法证明目标教学班已选中，仍须对照官方已选明细。"
            )
        return {
            "missing_session": "请先在 EXE 内登录并核验本人教务账号",
            "invalid_contract": "服务器地址、会话或请求配置不正确，未查询",
            "busy": "已有一次本人选课汇总查询正在执行",
            "cooldown": "选课汇总查询需至少间隔 60 秒",
            "expired": "学校提示会话失效，已停止",
            "restricted": "学校限制当前查询，未重试",
            "round_unavailable": "当前会话未返回完整的本人学号／学年／学期，未继续请求汇总接口",
            "score_unrecognized": "汇总接口已返回，但尚不能识别已选门数字段",
            "unavailable": "学校接口读取失败，没有修改任何选课记录",
            "policy_unverified": "已启用的学校请求预算缺少完整配置，未执行查询",
        }[self.code]


def _parse_json_record(raw: bytes) -> dict | None:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_RESPONSE:
        return None
    try:
        data = json.loads(raw.decode("utf-8-sig", "strict"))
        return data if isinstance(data, dict) else None
    except (UnicodeError, ValueError, RecursionError):
        return None


def _context_from_window(raw: bytes) -> dict[str, str] | None:
    record = _parse_json_record(raw)
    if record is None or str(record.get("status")) not in ("200", "400"):
        return None
    result = record.get("result")
    try:
        values = json.loads(result) if isinstance(result, str) else result
    except (ValueError, TypeError):
        return None
    if not isinstance(values, dict) or str(values.get("lx", "2")) != "2":
        return None
    needed = {}
    for key in ("xh", "xn", "xqM"):
        value = values.get(key)
        if (not isinstance(value, str) or not value.strip()
                or len(value) > 100
                or any(c in value for c in ("&", "=", "\\r", "\\n"))):
            return None
        needed[key] = value
    # xh is a sensitive account identifier; it is transient and is
    # neither returned by PersonalScoreCount nor logged.
    return needed


def _selected_count_from_score(raw: bytes) -> int | None:
    record = _parse_json_record(raw)
    if record is None or str(record.get("status")) != "200":
        return None
    result = record.get("result")
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    count = data.get("yxms")  # The field documented in the earlier first-party code.
    if isinstance(count, bool) or count is None:
        return None
    if isinstance(count, int):
        n = count
    elif isinstance(count, str) and count.isascii() and count.isdecimal():
        n = int(count)
    else:
        return None
    return n if 0 <= n <= 100 else None


async def read_personal_s202_score(
    config: Mapping, *, session_cookie: str | None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
) -> PersonalScoreCount:
    global _NEXT_ALLOWED_AT
    if not _validate_origin(config, session_cookie):
        return PersonalScoreCount(
            "missing_session" if not session_cookie else "invalid_contract")
    if not _LIMIT.acquire(blocking=False):
        return PersonalScoreCount("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_ALLOWED_AT:
                return PersonalScoreCount("cooldown")
            _NEXT_ALLOWED_AT = time.monotonic() + 60.0
        policy = config.get("server_policy", {})
        budget = None
        if isinstance(policy, Mapping) and policy.get("verified") is True:
            try:
                budget = budget_factory(dict(config), ("https", HOST))
            except Exception:
                return PersonalScoreCount("policy_unverified")
        if http_factory is None:
            http_factory = lambda: httpx.AsyncClient(
                follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            )
        async with http_factory() as client:
            for pair in session_cookie.split(";"):
                name, val = pair.strip().split("=", 1)
                client.cookies.set(name, val, domain=HOST, path="/hsjw")

            async def post(path: str, payload: bytes) -> httpx.Response:
                async def send():
                    return await client.post(
                        ORIGIN + path, content=payload,
                        headers={
                            "Referer": REFERER,
                            "Accept": "application/json,text/plain",
                            "Content-Type":
                                "application/x-www-form-urlencoded; charset=GBK",
                        },
                        follow_redirects=False,
                    )
                if budget is None:
                    return await send()
                async with budget.request():
                    return await send()

            async def validate(response: httpx.Response) -> str | None:
                status = response.status_code
                if status in (401, 302, 303, 307, 308):
                    return "expired"
                if status in (403, 429):
                    if status == 429 and budget is not None:
                        from core.client import KingoClient
                        delay = KingoClient._retry_after_seconds(
                            response.headers.get("Retry-After"))
                        await budget.impose_retry_after(delay or 60.0)
                    return "restricted"
                if status != 200:
                    return "unavailable"
                if response.content and len(response.content) <= _MAX_RESPONSE:
                    txt = response.content.decode("utf-8", "ignore")
                    if any(marker in txt for marker in _EXPIRED):
                        return "expired"
                return None

            window_resp = await post(WINDOW_PATH + "?xktype=2", b"")
            err = await validate(window_resp)
            if err:
                return PersonalScoreCount(err)
            scope = _context_from_window(window_resp.content)
            if scope is None:
                return PersonalScoreCount("round_unavailable")

            # Only the current official session-scope fields are sent.
            payload = encode_gbk_form([
                ("xn", scope["xn"]), ("xq_m", scope["xqM"]),
                ("xh", scope["xh"]),
            ])
            score_resp = await post(SCORE_PATH, payload)
            err = await validate(score_resp)
            if err:
                return PersonalScoreCount(err)
            count = _selected_count_from_score(score_resp.content)
            if count is None:
                return PersonalScoreCount("score_unrecognized", scope_verified=True)
            return PersonalScoreCount("count_observed", count, scope_verified=True)
    except (httpx.HTTPError, TypeError, ValueError, KeyError, UnicodeError):
        return PersonalScoreCount("unavailable")
    except Exception:
        return PersonalScoreCount("unavailable")
    finally:
        _LIMIT.release()
