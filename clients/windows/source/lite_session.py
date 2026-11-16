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
from core.ncwu_term_probe import probe_known_year_term, validate_term_contract
from lite_session_vault import WindowsSessionVault

HOST = "jwmis.ncwu.edu.cn"


@dataclass
class LiteSession:
    cookie: str = ""
    origin: str = ""
    status: str = "not_logged_in"
    vault: WindowsSessionVault | None = None
    username: str = ""

    def clear(self, *, forget: bool = False) -> dict[str, Any]:
        self.cookie = ""
        self.origin = ""
        self.status = "not_logged_in"
        self.username = ""
        if forget and self.vault is not None:
            self.vault.clear()
        return self.info()

    def info(self) -> dict[str, Any]:
        # A local encrypted token never proves a school session is still live.
        try:
            saved = self.vault.load() if self.vault is not None else None
        except (OSError, ValueError):
            saved = None
        return {
            "status": self.status,
            "verified_at_login": self.status == "verified",
            "persistent": bool(
                saved is not None and self.status == "verified"
                and saved.username == self.username
            ),
            "saved_session_available": saved is not None,
            "origin": self.origin if self.status == "verified" else "",
        }

    def restore(self, config: Mapping) -> dict[str, Any]:
        """One explicit user action; never trust a saved cookie without proof."""
        if self.status == "verified":
            return {"code": "already_active", "message": "本次应用内已有已核验会话",
                    "session": self.info()}
        saved = self.vault.load() if self.vault is not None else None
        if saved is None:
            return {"code": "no_saved_session", "message": "本机没有可恢复的加密会话",
                    "session": self.info()}
        identity = config.get("known_year_term", {})
        if (not isinstance(identity, Mapping)
                or identity.get("identity_binding_verified") is not True
                or validate_term_contract(config) is not None):
            return {"code": "verification_unavailable",
                    "message": "本机还没有通过学校身份回读协议核验；已保留加密会话，但不能认定登录有效",
                    "session": self.info()}
        proof = asyncio.run(probe_known_year_term(
            config, session_cookie=saved.cookie, expected_user_code=saved.username,
            environment={},
        ))
        if proof.code == "identity_verified":
            self.cookie = saved.cookie
            self.origin = HOST
            self.status = "verified"
            self.username = saved.username
            return {"code": "restored", "message": "已通过学校账号一致性核验，恢复本机保存的登录会话",
                    "session": self.info()}
        if proof.code in ("expired", "identity_mismatch"):
            self.clear(forget=True)
        return {"code": proof.code, "message": proof.message, "session": self.info()}

    def check(self, config: Mapping) -> dict[str, Any]:
        """User-triggered identity recheck of the existing in-memory session."""
        if self.status != "verified" or self.origin != HOST or not self.cookie or not self.username:
            return {"code": "missing_session", "message": "当前没有可复核的已登录教务会话",
                    "session": self.info()}
        contract = config.get("known_year_term", {})
        if (not isinstance(contract, Mapping)
                or contract.get("identity_binding_verified") is not True
                or validate_term_contract(config) is not None):
            return {"code": "verification_unavailable",
                    "message": "官方账号一致性回读协议尚未核验；未访问学校",
                    "session": self.info()}
        proof = asyncio.run(probe_known_year_term(
            config, session_cookie=self.cookie, expected_user_code=self.username,
            environment={},
        ))
        if proof.code in ("expired", "identity_mismatch"):
            self.clear(forget=True)
        return {
            "code": proof.code,
            "message": proof.message,
            "session": self.info(),
        }

    def login(self, config: Mapping, username: str, password: str, *,
              remember: bool = False) -> dict[str, Any]:
        # One manual action. The original signed login module enforces its
        # own 60-second local cooldown and official challenge requirements.
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("credentials must be text")
        if len(username) > 128 or len(password) > 256:
            raise ValueError("credentials too long")
        # Switching to a different account, or declining "remember", must
        # not silently leave another account's bearer token in Windows storage.
        # Fail closed if the old credential cannot be safely revoked.
        if self.vault is not None:
            existing = self.vault.load()
            if existing is not None and (existing.username != username or not remember):
                self.vault.clear()
        self.clear()
        result = asyncio.run(login_ncwusigned(config, username, password))
        saved_error = False
        if result.verified and result.session_origin == HOST:
            self.cookie = result.session_cookie
            self.origin = result.session_origin
            self.status = "verified"
            self.username = username
            if self.vault is not None:
                try:
                    if remember:
                        self.vault.save(username, result.session_cookie)
                    else:
                        self.vault.clear()
                except (OSError, ValueError):
                    saved_error = True
            elif remember:
                saved_error = True
        return {
            "code": result.code,
            "message": result.message + ("；本机加密保存失败，仅当前运行期间有效" if saved_error else ""),
            "remember_error": saved_error,
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
            # A school-expired session must not remain available for a later
            # passwordless restore. Never keep a known-rejected bearer token.
            self.clear(forget=result.code == "expired")
        return {
            "code": result.code,
            "message": result.message,
            "window": result.window,
            "stage": result.stage,
            "total_courses": len(result.courses) if result.code == "catalog_read" else 0,
            "display_limit": 200,
            "courses": [
                {
                    "course_name": item.course_name,
                    "course_code": item.course_code,
                    "class_number": item.class_number,
                    "teacher": item.teacher,
                    "selection_status": item.selection_status,
                    "points_label": item.points_label,
                }
                for item in result.courses[:200]
            ] if result.code == "catalog_read" else [],
            "session": self.info(),
            "data_source": "live_school" if result.code == "catalog_read" else "unavailable",
            "seat_count_verified": False,
        }
