"""One-shot, current-session NCWU S202 selection-round readback.

The user's 2026-11-16 first-party evidence and independently reviewed code
in 07xh/kingo-live confirm the read-only window endpoint with xktype=2.
The response may include current-round fields even when the window is closed.
Only non-identity metadata is returned to the GUI; no account ID, round ID,
encrypted values or other session scope leaks into logs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from typing import Callable, Mapping

import httpx

from core.ncwu_live_elective import (
    HOST, ORIGIN, REFERER, WINDOW_PATH, _validate_origin,
)
from core.request_budget import build_verified_budget
from core.ncwu_read_support import (
    default_read_client, set_scoped_cookie,
    get_optional_verified_budget, classify_read_response,
)

_LOCK = threading.Lock()
_NEXT_ALLOWED_AT = 0.0
_FIELDS = ("xh", "xn", "xqM", "lcid", "nj", "zydm")
_EXPIRED = ("凭证已失效", "登录已失效", "请先登录", "请重新登录")


@dataclass(frozen=True)
class S202RoundStatus:
    code: str
    window: str = "unknown"
    field_count: int = 0
    current_context_complete: bool = False
    round_name: str = ""
    term_label: str = ""
    start_label: str = ""
    end_label: str = ""

    @property
    def message(self) -> str:
        if self.code == "observed":
            status_text = {
                "closed": "尚未开放",
                "reported_open": "接口报告开放（未核对课程）",
                "unknown": "开放状态未知",
                "inconsistent": "服务器字段存在矛盾，不能认定开放",
            }[self.window]
            return (
                f"S202 轮次查询：{status_text}；"
                f"轮次：{self.round_name or '未提供'}；"
                f"学期：{self.term_label or '未提供'}；"
                f"起止：{self.start_label or '未提供'} / "
                f"{self.end_label or '未提供'}；"
                f"本次会话上下文字段 {self.field_count}/6。"
                "不表示可选课程或自动提交已可用。"
            )
        return {
            "missing_session": "请先使用 EXE 自身登录本人账号",
            "invalid_contract": "学校主机或会话格式不匹配；未访问学校",
            "busy": "已有 S202 轮次查询正在执行",
            "cooldown": "轮次查询间隔至少 60 秒，请勿重复点击",
            "policy_unverified": "请求预算配置无效，未访问学校",
            "expired": "教务会话失效，需重新登录",
            "restricted": "学校拒绝或限制本次查询，已停止",
            "invalid_response": "轮次接口返回格式无法识别，不推测开放状态",
            "unavailable": "无法读取当前轮次，未自动重试",
        }[self.code]


def _short(value) -> str:
    """Only bounded non-sensitive official round labels reach the UI."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:90]


def parse_s202_round(raw: bytes) -> S202RoundStatus:
    if not isinstance(raw, bytes) or not raw or len(raw) > 16384:
        return S202RoundStatus("invalid_response")
    try:
        message = raw.decode("utf-8-sig", errors="strict")
        if any(marker in message for marker in _EXPIRED):
            return S202RoundStatus("expired")
        result = json.loads(message)
        if not isinstance(result, dict):
            return S202RoundStatus("invalid_response")
        status = str(result.get("status", ""))
        if status not in ("200", "400"):
            return S202RoundStatus("invalid_response")
        original = result.get("result")
        context = json.loads(original) if isinstance(original, str) else original
        if not isinstance(context, dict):
            return S202RoundStatus("invalid_response")
        if context.get("lx") not in (None, "2", 2):
            return S202RoundStatus("invalid_response")
        available = sum(
            isinstance(context.get(k), str) and bool(context[k].strip())
            and len(context[k]) <= 512 for k in _FIELDS
        )
        is_valid = str(context.get("isValidTimerange", "")).lower()
        if status == "200":
            window = "reported_open" if is_valid not in ("0", "false") else "inconsistent"
        elif "选课时间" in str(result.get("message", "")):
            window = "closed" if is_valid not in ("1", "true") else "inconsistent"
        else:
            window = "unknown"
        return S202RoundStatus(
            "observed", window, available,
            window == "reported_open" and available == len(_FIELDS),
            round_name=_short(context.get("lcmc")),
            term_label=_short(context.get("xnxqDesc") or context.get("xqName")),
            start_label=_short(context.get("qssj")),
            end_label=_short(context.get("jssj")),
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return S202RoundStatus("invalid_response")


async def probe_s202_round(
    config: Mapping, *, session_cookie: str | None,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    budget_factory: Callable = build_verified_budget,
    enforce_manual_cooldown: bool = True,
) -> S202RoundStatus:
    """One direct first-party POST. No enrollment, cancellation or polling."""
    global _NEXT_ALLOWED_AT
    if not _validate_origin(config, session_cookie):
        return S202RoundStatus(
            "missing_session" if not session_cookie else "invalid_contract")
    if not _LOCK.acquire(blocking=False):
        return S202RoundStatus("busy")
    try:
        if enforce_manual_cooldown:
            if time.monotonic() < _NEXT_ALLOWED_AT:
                return S202RoundStatus("cooldown")
            _NEXT_ALLOWED_AT = time.monotonic() + 60.0
        try:
            budget = get_optional_verified_budget(config, budget_factory)
        except Exception:
            return S202RoundStatus("policy_unverified")
        if http_factory is None:
            http_factory = default_read_client
        async with http_factory() as client:
            set_scoped_cookie(client, session_cookie)
            async def send():
                return await client.post(
                    ORIGIN + WINDOW_PATH + "?xktype=2",
                    headers={
                        "Referer": REFERER,
                        "Accept": "text/plain,application/json",
                    }, follow_redirects=False,
                )
            if budget is not None:
                async with budget.request():
                    response = await send()
            else:
                response = await send()
            error = await classify_read_response(response, budget)
            if error:
                return S202RoundStatus(error)
            return parse_s202_round(response.content)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return S202RoundStatus("unavailable")
    except Exception:
        return S202RoundStatus("unavailable")
    finally:
        _LOCK.release()
