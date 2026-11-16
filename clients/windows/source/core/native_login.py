"""Native, explicitly authorized form authentication for the Windows EXE.

This is a constrained same-origin form adapter, not a guessed NCWU/CAS protocol.
Only an independently verified official login contract may send one form POST.
No browser automation, credential persistence, challenge solving, retries or
enrollment writes are performed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from core.client import AuthenticationError, KingoClient, RateLimitError
from core.request_budget import build_verified_budget
from core.ncwu_term_probe import validate_term_contract, probe_known_year_term

_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_COOKIE_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")


@dataclass(frozen=True)
class NativeLoginResult:
    code: str
    # Never include a credential in repr, logs, return message or settings.
    session_cookie: str = field(default="", repr=False, compare=False)
    session_origin: str = field(default="", repr=False, compare=False)

    @property
    def verified(self) -> bool:
        return self.code == "verified" and bool(self.session_cookie) and bool(self.session_origin)

    @property
    def message(self) -> str:
        return {
            "protocol_unverified": "学校身份接口尚未核验；未执行 EXE 登录",
            "identity_unverified": "学期账号身份回读协议尚未核验；未发送账号密码",
            "identity_mismatch": "登录后的学期接口账号标识与本次输入不一致；已丢弃会话",
            "login_unverified": "EXE 登录协议尚未核验；未发送账号密码",
            "loginmethod_unverified": "旧版学校登录包含 loginmethod，但必需取值尚未核验；未发送账号密码",
            "login_variant_unverified": "11月3日与11月14日登录请求格式不同；请先核验所属协议版本，未发送账号密码",
            "signed_variant_unsupported": "观察到新版动态登录参数，但 EXE 暂无已核验的生成协议；未请求密码",
            "cas_adapter_missing": "已选择学校统一认证 CAS；目前未接入跨域认证协议，未请求密码",
            "invalid_contract": "登录协议字段不完整或不安全；未发送账号密码",
            "invalid_credentials": "请输入有效的本人账号和密码；未联网",
            "policy_unverified": "没有已核验的官方请求规则；未发送账号密码",
            "challenge_required": "官方登录要求额外验证；请使用学校允许的认证方式",
            "expired": "登录凭据未获得官方身份确认；没有保存会话",
            "restricted": "官方服务拒绝访问或限制请求；已停止登录",
            "unavailable": "EXE 登录未成功完成身份核验；没有保存会话",
            "verified": "EXE 自有登录会话已通过官方身份核验；尚不代表可自动提交选课",
        }[self.code]


def _contract(config: Mapping) -> tuple[str | None, tuple | None]:
    auth = config.get("auth")
    login = auth.get("login") if isinstance(auth, dict) else None
    identity_mode = (login.get("identity_mode", "configured_identity")
                     if isinstance(login, dict) else "configured_identity")
    if identity_mode == "year_term":
        # Validated, known, no-body school action. Presence of userCode alone
        # is insufficient: caller must also bind it to the entered account.
        if validate_term_contract(config) is not None:
            return "identity_unverified", None
    elif identity_mode == "configured_identity":
        protocol = config.get("protocol")
        if not isinstance(protocol, dict) or protocol.get("verified") is not True:
            return "protocol_unverified", None
        for key in ("identity_path", "identity_selector", "identity_referer"):
            if not protocol.get(key):
                return "protocol_unverified", None
    else:
        return "invalid_contract", None
    if isinstance(login, dict) and login.get("mode") == "cas":
        # A public school CAS entry is NOT a verified cross-origin login
        # protocol, and must not be sent to the single-origin form client.
        return "cas_adapter_missing", None
    if not isinstance(login, dict) or login.get("verified") is not True:
        return "login_unverified", None
    if login.get("mode", "direct_form") != "direct_form":
        return "invalid_contract", None
    base = config.get("base_url")
    if not isinstance(base, str):
        return "invalid_contract", None
    parts = urlsplit(base)
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        return "invalid_contract", None
    path = login.get("path")
    user_field = login.get("username_field")
    pass_field = login.get("password_field")
    extras = login.get("static_fields", {})
    if (login.get("method") != "POST"
            or not isinstance(path, str) or not path.startswith("/")
            or path.startswith("//") or "?" in path or "#" in path
            or any(segment == ".." for segment in path.split("/"))
            or len(path) > 300
            or not isinstance(user_field, str) or not _FIELD.fullmatch(user_field)
            or not isinstance(pass_field, str) or not _FIELD.fullmatch(pass_field)
            or user_field == pass_field
            or not isinstance(extras, dict) or len(extras) > 12):
        return "invalid_contract", None
    # 2026-11-03 school HAR explicitly observed loginmethod alongside
    # username/password; never silently omit it in the known form contract.
    if parts.hostname == "jwmis.ncwu.edu.cn" and path == "/hsjw/cas/logon.action":
        # Two distinct legitimate request shapes were observed in the user's
        # November 03 and November 14 captures. Neither may be guessed.
        variant = login.get("contract_variant")
        if variant == "signed_20261114":
            return "signed_variant_unsupported", None
        if variant != "plain_20261103":
            return "login_variant_unverified", None
        if ("loginmethod" not in extras or not isinstance(extras["loginmethod"], str)
                or not extras["loginmethod"].strip()):
            return "loginmethod_unverified", None
    for key, value in extras.items():
        if (not isinstance(key, str) or not _FIELD.fullmatch(key)
                or key in (user_field, pass_field)
                or not isinstance(value, str) or len(value) > 256
                or "\r" in value or "\n" in value):
            return "invalid_contract", None
    url = urljoin(base.rstrip("/") + "/", path)
    dest = urlsplit(url)
    if (dest.scheme, dest.netloc) != (parts.scheme, parts.netloc):
        return "invalid_contract", None
    return None, (url, user_field, pass_field, dict(extras), parts.hostname, parts.netloc)


def validate_login_contract(config: Mapping) -> str | None:
    """Pure no-network gate; code is None only for structurally verified config."""
    return _contract(config)[0]


def _same_origin_cookies(client: httpx.AsyncClient, host: str) -> str:
    seen: set[str] = set()
    pairs = []
    for item in client.cookies.jar:
        domain = item.domain.lstrip(".").lower()
        if domain and domain != host.lower():
            continue  # Never export unrelated SSO or third-party cookies.
        name, value = item.name, item.value
        if (not _COOKIE_NAME.fullmatch(name) or not isinstance(value, str)
                or not value or len(value) > 2048 or any(ch in value for ch in ";\r\n")):
            continue
        if name in seen:
            return ""  # Duplicate names with different paths are ambiguous.
        seen.add(name)
        pairs.append(f"{name}={value}")
    result = "; ".join(pairs)
    return result if len(result) <= 4096 else ""


async def login_with_verified_form(
    config: Mapping, username: str, password: str, *,
    transport_factory: Callable[[], httpx.AsyncClient] | None = None,
    identity_factory: Callable[..., KingoClient] = KingoClient,
) -> NativeLoginResult:
    """One explicitly confirmed POST and one independent identity read.

    Verified settings and a server-policy budget are required before HTTP.
    The resulting cookie is volatile and must stay in the application process.
    """
    error, parameters = _contract(config)
    if error:
        return NativeLoginResult(error)
    if (not isinstance(username, str) or not username.strip() or len(username) > 128
            or not isinstance(password, str) or not password or len(password) > 256):
        return NativeLoginResult("invalid_credentials")
    url, user_field, pass_field, extras, host, netloc = parameters
    try:
        budget = build_verified_budget(dict(config), ("https", netloc))
    except Exception:
        return NativeLoginResult("policy_unverified")
    form = {**extras, user_field: username, pass_field: password}
    if transport_factory is None:
        transport_factory = lambda: httpx.AsyncClient(
            http1=True, http2=False, follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
    client = None
    try:
        async with transport_factory() as transport:
            async with budget.request():
                response = await transport.post(
                    url, data=form, follow_redirects=False,
                    headers={"Accept": "text/html,application/json"},
                )
                if response.status_code == 429:
                    # Must preserve server cooldown semantics; this function
                    # never retries or probes another login endpoint.
                    retry_after = KingoClient._retry_after_seconds(
                        response.headers.get("Retry-After"))
                    await budget.impose_retry_after(retry_after if retry_after is not None else 60.0)
                    return NativeLoginResult("restricted")
            if response.status_code in (401, 403):
                return NativeLoginResult("restricted" if response.status_code == 403 else "expired")
            if response.status_code >= 400 or len(response.content) > 1024 * 1024:
                return NativeLoginResult("unavailable")
            if 300 <= response.status_code < 400:
                redirect = urljoin(url, response.headers.get("location", ""))
                dest = urlsplit(redirect)
                if (dest.scheme, dest.netloc) != ("https", netloc):
                    return NativeLoginResult("challenge_required")
                # Redirects are never followed. Only independent identity proof
                # can establish whether this response authenticated.
            if "验证码" in response.text or b"captcha" in response.content.lower():
                return NativeLoginResult("challenge_required")
            cookie = _same_origin_cookies(transport, host)
            if not cookie:
                return NativeLoginResult("unavailable")
        identity_mode = config["auth"]["login"].get("identity_mode", "configured_identity")
        if identity_mode == "year_term":
            term = await probe_known_year_term(
                config, session_cookie=cookie, expected_user_code=username,
            )
            if term.code == "identity_verified":
                return NativeLoginResult("verified", cookie, netloc)
            if term.code == "identity_mismatch":
                return NativeLoginResult("identity_mismatch")
            if term.code == "restricted":
                return NativeLoginResult("restricted")
            if term.code == "expired":
                return NativeLoginResult("expired")
            return NativeLoginResult("unavailable")
        client = identity_factory(dict(config), session_cookie=cookie)
        await client.identity()
        return NativeLoginResult("verified", cookie, netloc)
    except AuthenticationError:
        return NativeLoginResult("expired")
    except RateLimitError:
        return NativeLoginResult("restricted")
    except Exception:
        return NativeLoginResult("unavailable")
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
