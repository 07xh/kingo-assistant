"""Shared transport primitives for read-only NCWU S202 HTTP adapters.

This module deliberately knows no course-selection endpoint or response
business contract. It centralizes existing first-party session scoping,
bounded transport defaults, optional server policy, and 401/429 handling.
Callers retain their own request headers, field parsing and cooldowns.
"""
from __future__ import annotations

from typing import Callable, Mapping

import httpx

from core.client import KingoClient

HOST = "jwmis.ncwu.edu.cn"


def default_read_client() -> httpx.AsyncClient:
    """Same timeouts, redirects, proxy isolation and connection cap as before."""
    return httpx.AsyncClient(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    )


def set_scoped_cookie(
    client: httpx.AsyncClient, cookie: str, *,
    path: str = "/hsjw",
) -> None:
    """Install *only* an already-validated EXE session; never persist it.

    Strict origin / cookie syntax validation remains at each public adapter
    entry point. This helper keeps the same cookie domain and order as the
    prior inlined implementations.
    """
    for pair in cookie.split(";"):
        name, value = pair.strip().split("=", 1)
        client.cookies.set(name, value, domain=HOST, path=path)


def get_optional_verified_budget(
    config: Mapping, budget_factory: Callable,
):
    """Preserve existing budget semantics; fail closed if verified is invalid."""
    policy = config.get("server_policy", {})
    if isinstance(policy, Mapping) and policy.get("verified") is True:
        return budget_factory(dict(config), ("https", HOST))
    return None


async def classify_read_response(
    response: httpx.Response, budget=None, *, max_bytes: int | None = None,
    require_content: bool = False, expired_markers: tuple[str, ...] = (),
    expired_scan_max_bytes: int | None = None,
) -> str | None:
    """Return transport-level category only; business parsing stays at caller.

    403/429 terminate without a retry. If an externally verified budget is
    configured, a 429 imposes Retry-After (fallback 60 s) on that budget.
    """
    status = response.status_code
    if status in (401, 302, 303, 307, 308):
        return "expired"
    if status in (403, 429):
        if status == 429 and budget is not None:
            delay = KingoClient._retry_after_seconds(
                response.headers.get("Retry-After"))
            await budget.impose_retry_after(delay or 60.0)
        return "restricted"
    if status != 200:
        return "unavailable"
    content = response.content
    if max_bytes is not None and len(content) > max_bytes:
        return "unavailable"
    if require_content and not content:
        return "unavailable"
    if expired_markers and content and (expired_scan_max_bytes is None or len(content) <= expired_scan_max_bytes):
        text = content.decode("utf-8", errors="ignore")
        if any(word in text for word in expired_markers):
            return "expired"
    return None
