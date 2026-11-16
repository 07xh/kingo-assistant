"""In-memory, explicit-action school session for KingoAssistant Lite.

The UI never receives credentials or cookies. This session is private to the
sidecar process and disappears when it exits. No login or school requests are
made automatically; no scheduler, captcha solver, or write adapter is present.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from core.ncwu_signed_login import login_ncwusigned
from core.ncwu_live_elective import live_s202_snapshot

HOST = "jwmis.ncwu.edu.cn"


@dataclass
class LiteSession:
    cookie: str = ""
    origin: str = ""
    status: str = "not_logged_in"

    def clear(self) -> dict[str, Any]:
        self.cookie = ""
        self.origin = ""
        self.status = "not_logged_in"
        return self.info()

    def info(self) -> dict[str, Any]:
        # A login-time identity proof is not a continuous session guarantee.
        return {
            "status": self.status,
            "verified_at_login": self.status == "verified",
            "persistent": False,
            "origin": self.origin if self.status == "verified" else "",
        }

    def login(self, config: Mapping, username: str, password: str) -> dict[str, Any]:
        # One manual action. The original signed login module enforces its
        # own 60-second local cooldown and official challenge requirements.
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("credentials must be text")
        if len(username) > 128 or len(password) > 256:
            raise ValueError("credentials too long")
        self.clear()
        result = asyncio.run(login_ncwusigned(config, username, password))
        if result.verified and result.session_origin == HOST:
            self.cookie = result.session_cookie
            self.origin = result.session_origin
            self.status = "verified"
        return {
            "code": result.code,
            "message": result.message,
            "session": self.info(),
        }

    def catalog(self, config: Mapping) -> dict[str, Any]:
        if self.status != "verified" or not self.cookie or self.origin != HOST:
            return {
                "code": "missing_session",
                "message": "请先在 Lite 内完成本人账号登录",
                "window": "unknown",
                "courses": [],
                "session": self.info(),
            }
        # The source adapter performs one manually-triggered read-only
        # snapshot, with a persistent cooldown and school policy checks.
        result = asyncio.run(live_s202_snapshot(
            config, session_cookie=self.cookie,
        ))
        if result.code in ("expired", "invalid_contract"):
            self.clear()
        return {
            "code": result.code,
            "message": result.message,
            "window": result.window,
            "stage": result.stage,
            "courses": [
                {
                    "course_name": item.course_name,
                    "course_code": item.course_code,
                    "class_number": item.class_number,
                    "teacher": item.teacher,
                    "selection_status": item.selection_status,
                    "points_label": item.points_label,
                }
                for item in result.courses
            ] if result.code == "catalog_read" else [],
            "session": self.info(),
            "data_source": "live_school" if result.code == "catalog_read" else "unavailable",
            "seat_count_verified": False,
        }
