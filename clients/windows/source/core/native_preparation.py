"""Explicit in-process read-only API preparation for the Windows EXE.

Never opens a browser, guesses NCWU endpoints, imports browser cookies or
attempts enrollment. A verified institutional catalog contract and a current
application-owned session are prerequisites for every network call.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Mapping

from core.client import (
    AuthenticationError, KingoClient, RateLimitError,
)

_REQUIRED_PROTOCOL_FIELDS = (
    "identity_path", "identity_referer", "identity_selector",
    "entry", "entry_referer", "list", "list_method",
    "term_field", "columns", "table_marker",
)
_TEMPLATE_IDS = frozenset({"FILL_FROM_OFFICIAL_PAGE", "SECOND_OPTION_ID"})


@dataclass(frozen=True)
class NativePreparationResult:
    code: str
    catalog_rows: int = 0
    target_matches: int = 0
    known_remaining: int = 0

    @property
    def message(self) -> str:
        return {
            "protocol_unverified": "未核验华水课程查询协议；EXE 未访问学校",
            "protocol_incomplete": "学校课程查询协议字段不完整；EXE 未访问学校",
            "missing_session": "EXE 尚无本人登录会话；不会从浏览器读取 Cookie",
            "invalid_plan": "请先设置真实课程代码和选课学期；EXE 未访问学校",
            "expired": "EXE 登录会话已失效；需要重新授权",
            "restricted": "学校拒绝访问或限制请求；已停止接口预检",
            "unavailable": "无法核验本人身份或课程目录；未启用自动提交",
            "ready": (
                f"EXE 接口预检通过：读取 {self.catalog_rows} 条课程记录，"
                f"精确匹配 {self.target_matches} 条，"
                f"其中 {self.known_remaining} 条有明确余量；"
                "不代表选课窗口开放或自动提交已可用"
            ),
        }[self.code]


async def prepare_native_api(
    config: Mapping, *,
    environment: Mapping[str, str] | None = None,
    factory: Callable[[dict], KingoClient] = KingoClient,
    session_cookie: str | None = None,
) -> NativePreparationResult:
    """Check identity and query catalog with the EXE's own HTTP client.

    The user must explicitly invoke this operation. No automatic login or
    write capability is implied. All errors are classified without exposing
    credentials, URLs, personal names or response bodies to the GUI.
    """
    protocol = config.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("verified") is not True:
        return NativePreparationResult("protocol_unverified")
    if any(not protocol.get(field) for field in _REQUIRED_PROTOCOL_FIELDS):
        return NativePreparationResult("protocol_incomplete")

    env = os.environ if environment is None else environment
    auth = config.get("auth")
    cookie = (session_cookie if session_cookie is not None else
              (env.get("KINGO_COOKIE") or
               (auth.get("cookie") if isinstance(auth, dict) else None)))
    if not isinstance(cookie, str) or not cookie.strip():
        return NativePreparationResult("missing_session")

    term = config.get("xnxq")
    courses = config.get("courses")
    if not isinstance(term, str) or not term.strip() or not isinstance(courses, list):
        return NativePreparationResult("invalid_plan")
    targets: set[tuple[str, str]] = set()
    for item in courses:
        if not isinstance(item, dict):
            return NativePreparationResult("invalid_plan")
        course_id = item.get("course_id")
        class_id = item.get("class_id")
        if not isinstance(course_id, str) or not course_id.strip() or course_id in _TEMPLATE_IDS:
            return NativePreparationResult("invalid_plan")
        if class_id is not None and not isinstance(class_id, str):
            return NativePreparationResult("invalid_plan")
        targets.add((course_id.strip(), (class_id or "").strip()))
    if not targets:
        return NativePreparationResult("invalid_plan")

    client = None
    try:
        client = (factory(dict(config), session_cookie=cookie)
                  if session_cookie is not None else factory(dict(config)))
        # This must be an authenticated official identity read, not a homepage
        # HTTP 200 and not a "Cookie exists" heuristic.
        await client.identity()
        # One normal catalog query, never a hidden polling loop or write.
        rows = await client.fetch_course_list(term, sorted({target[0] for target in targets}))
        matches = [
            row for row in rows
            if (row.course_id, row.class_id or "") in targets
            or (row.course_id, "") in targets
        ]
        return NativePreparationResult(
            "ready",
            catalog_rows=len(rows),
            target_matches=len(matches),
            known_remaining=sum(row.remain is not None for row in matches),
        )
    except AuthenticationError:
        return NativePreparationResult("expired")
    except RateLimitError:
        return NativePreparationResult("restricted")
    except Exception:
        return NativePreparationResult("unavailable")
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                # Cleanup must not turn an unknown school response into a
                # success or leak a response body through Tk logs.
                pass
