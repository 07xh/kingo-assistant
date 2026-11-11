"""Explicit, read-only verification of the application's own official session.

Browser cookies are deliberately NOT extracted or shared with this client.
A positive result requires a verified identity endpoint and a successful
authenticated identity GET, not merely a present Cookie or an opened browser.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Callable, Mapping

from core.client import (
    AuthenticationError, KingoClient, ProtocolError, RateLimitError,
)


@dataclass(frozen=True)
class SessionProbeResult:
    code: str
    verified: bool

    @property
    def message(self) -> str:
        return {
            "verified": "脚本会话已通过官方只读身份核验；不代表选课接口已可用",
            "protocol_unverified": "官方只读身份接口尚未完成核验，未请求学校",
            "missing_session": "脚本没有授权会话；浏览器登录不会自动导入脚本",
            "expired": "脚本登录会话已失效，请通过官方登录重新授权",
            "restricted": "学校拒绝访问或触发限制，已停止核验",
            "unavailable": "无法安全验证脚本会话；未据此启用选课",
        }[self.code]


async def probe_own_session(
    config: Mapping, *, environment: Mapping[str, str] | None = None,
    factory: Callable[[dict], KingoClient] = KingoClient,
) -> SessionProbeResult:
    """Perform at most one identity operation using an explicitly verified adapter.

    The underlying read-only client handles bounded transport failures; this
    function never submits enrollment, handles CAPTCHA or stores credentials.
    No raw response, identity or token is returned.
    """
    protocol = config.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("verified") is not True:
        return SessionProbeResult("protocol_unverified", False)
    env = os.environ if environment is None else environment
    auth = config.get("auth")
    cookie = (env.get("KINGO_COOKIE") or
              (auth.get("cookie") if isinstance(auth, dict) else None))
    if not isinstance(cookie, str) or not cookie.strip():
        return SessionProbeResult("missing_session", False)
    client = None
    try:
        client = factory(dict(config))
        await client.identity()
        return SessionProbeResult("verified", True)
    except AuthenticationError:
        return SessionProbeResult("expired", False)
    except RateLimitError:
        return SessionProbeResult("restricted", False)
    except Exception:
        # Do not leak raw exceptions, URLs, HTML, student identifiers or tokens.
        return SessionProbeResult("unavailable", False)
    finally:
        if client is not None:
            await client.aclose()
