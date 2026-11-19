"""Private, offline structural audit of the school's observed login routes.

HAR files often include passwords, session cookies, tickets and student data.
This module never exports a field, endpoint guess, request body, token or
original URL. It only emits fixed labels and bounded aggregate counts.
It never contacts NCWU or marks any production adapter as verified.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.har_audit import MAX_ENTRIES, MAX_HAR_BYTES

_AUTH_HOST = "authserver.ncwu.edu.cn"
_SCHOOL_HOST = "jwmis.ncwu.edu.cn"
_AUTH_PATH = "/authserver/login"
_SCHOOL_CALLBACK = "/hsjw/caslogin"


@dataclass(frozen=True, slots=True)
class AuthRouteSummary:
    entries_total: int
    cas_login_page_requests: int
    cas_auth_post_requests: int
    cas_school_service_links: int
    school_cas_callback_requests: int
    school_https_requests: int
    non_school_entries_ignored: int

    @property
    def observed_route(self) -> str:
        if self.cas_login_page_requests or self.cas_school_service_links:
            return "cas_observed"
        if self.school_cas_callback_requests:
            return "cas_callback_only"
        if self.school_https_requests:
            return "school_only"
        return "no_evidence"

    @property
    def message(self) -> str:
        label = {
            "cas_observed": "观察到学校统一认证 CAS 路径",
            "cas_callback_only": "只观察到教务 CAS 回调，缺少认证页请求",
            "school_only": "仅观察到教务 HTTPS 请求，不能确定使用哪种登录",
            "no_evidence": "没有足以识别登录方式的华水 HTTPS 请求",
        }[self.observed_route]
        return (
            f"本机 HAR 请求：{self.entries_total} 条\n"
            f"认证页面请求：{self.cas_login_page_requests} 条\n"
            f"认证服务 POST：{self.cas_auth_post_requests} 条（不代表成功）\n"
            f"教务 CAS service 指向：{self.cas_school_service_links} 条\n"
            f"教务 CAS 回调请求：{self.school_cas_callback_requests} 条\n"
            f"教务 HTTPS 请求：{self.school_https_requests} 条\n"
            f"忽略其他域名/不安全记录：{self.non_school_entries_ignored} 条\n\n"
            f"识别结果：{label}\n"
            "不解析账号凭据、会话凭据、身份票据、验证码或响应内容。"
            "不是登录成功、选课接口或剩余名额已核验的证明。"
        )


def _is_school_service(candidate: str) -> bool:
    if not isinstance(candidate, str) or len(candidate) > 2048:
        return False
    try:
        address = urlsplit(candidate)
        return (
            address.scheme == "https"
            and address.hostname == _SCHOOL_HOST
            and address.port in (None, 443)
            and address.path.rstrip("/") == _SCHOOL_CALLBACK
            and address.username is None and address.password is None
        )
    except ValueError:
        return False


def _is_auth_route(value: str) -> bool:
    try:
        address = urlsplit(value)
        return (address.scheme == "https"
                and address.hostname == _AUTH_HOST
                and address.port in (None, 443)
                and address.path.rstrip("/") == _AUTH_PATH
                and address.username is None and address.password is None)
    except (TypeError, ValueError):
        return False


def _contains_service(url: str) -> bool:
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True, max_num_fields=32)
    except (ValueError, TypeError):
        return False
    return any(_is_school_service(value) for value in query.get("service", ()))


def summarize_auth_route_har(path: Path) -> AuthRouteSummary:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_HAR_BYTES:
        raise ValueError("HAR 不存在或超过安全限制")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError, OSError) as exc:
        raise ValueError("HAR 格式无效") from exc
    entries = data.get("log", {}).get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("HAR entries 无效或超过容量上限")

    auth_pages = auth_posts = service_links = callbacks = school_requests = ignored = 0
    for entry in entries:
        req = entry.get("request") if isinstance(entry, dict) else None
        if not isinstance(req, dict):
            ignored += 1
            continue
        raw = req.get("url")
        if not isinstance(raw, str) or len(raw) > 8192:
            ignored += 1
            continue
        try:
            u = urlsplit(raw)
            is_https = u.scheme == "https" and u.port in (None, 443)
            acceptable = u.username is None and u.password is None
        except ValueError:
            ignored += 1
            continue
        if not (is_https and acceptable):
            ignored += 1
            continue
        method = req.get("method")
        if u.hostname == _AUTH_HOST and u.path.rstrip("/") == _AUTH_PATH:
            auth_pages += 1
            if method == "POST":
                auth_posts += 1
            if _contains_service(raw):
                service_links += 1
        elif u.hostname == _SCHOOL_HOST:
            school_requests += 1
            if u.path.rstrip("/") == _SCHOOL_CALLBACK:
                callbacks += 1
            # A 302 Location header is only a structural lead, never a
            # trustworthy authenticated identity proof.
            resp = entry.get("response")
            if isinstance(resp, dict):
                headers = resp.get("headers")
                if isinstance(headers, list) and len(headers) <= 100:
                    for item in headers:
                        if (isinstance(item, dict)
                                and str(item.get("name", "")).lower() == "location"
                                and isinstance(item.get("value"), str)
                                and len(item["value"]) <= 8192):
                            loc = item["value"]
                            if _is_auth_route(loc) and _contains_service(loc):
                                service_links += 1
        else:
            ignored += 1
    return AuthRouteSummary(
        len(entries), auth_pages, auth_posts, service_links,
        callbacks, school_requests, ignored
    )
