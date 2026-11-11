"""NCWU official login surfaces, opt-in reachability diagnostics and route planning.

A login-page HTTP 200 is NOT proof of an authenticated elective catalog. The
catalog selector consumes only verified read-only adapter observations, never
login-page probes. The shipped registry has ONE catalog candidate; automatic
live failover therefore remains disabled until another *officially documented*
route and its separate session/protocol contract have been verified.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

import httpx


EVIDENCE_URL = "https://www2.ncwu.edu.cn/jiaowu/info/1008/2973.htm"


class EntryKind(str, Enum):
    WEB_DIRECT = "web_direct"
    SSO_BROWSER = "sso_browser"
    MOBILE_APP = "mobile_app"


@dataclass(frozen=True, slots=True)
class OfficialEntry:
    identifier: str
    name: str
    kind: EntryKind
    launch_url: str | None
    catalog_candidate: bool
    evidence_url: str = EVIDENCE_URL


# URLs in this inventory are public school login surfaces, not guessed APIs,
# physical-node addresses, IP bypasses, or verified enrollment endpoints.
OFFICIAL_ENTRIES: tuple[OfficialEntry, ...] = (
    OfficialEntry(
        "ncwu_web", "教务系统直接登录", EntryKind.WEB_DIRECT,
        "https://jwmis.ncwu.edu.cn/", True
    ),
    OfficialEntry(
        "ncwu_sso", "学校统一身份认证", EntryKind.SSO_BROWSER,
        "https://authserver.ncwu.edu.cn/authserver/login?"
        "service=https%3A%2F%2Fjwmis.ncwu.edu.cn%2Fhsjw%2Fcaslogin", False
    ),
    OfficialEntry(
        "ncwu_xiqueer", "喜鹊儿（官方 App）", EntryKind.MOBILE_APP,
        None, False
    ),
)


class ProbeState(str, Enum):
    REACHABLE = "reachable"
    AUTH_REQUIRED = "auth_required"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    CONNECTION_ERROR = "connection_error"
    APP_ONLY = "app_only"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class LoginProbe:
    entry_id: str
    state: ProbeState
    http_status: int | None = None
    elapsed_ms: float | None = None


async def probe_official_login_surfaces(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[LoginProbe, ...]:
    """Manually invoked: at most one unauthenticated GET per public web entry.

    No redirects, credentials, cookies, response bodies, endpoint discovery,
    retry loop or parallel requests. NEVER use these results to select a
    catalog/enrollment backend.
    """
    results: list[LoginProbe] = []
    halted = False
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(5.0),
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    ) as client:
        for entry in OFFICIAL_ENTRIES:
            if entry.launch_url is None:
                results.append(LoginProbe(entry.identifier, ProbeState.APP_ONLY))
                continue
            if halted:
                results.append(LoginProbe(entry.identifier, ProbeState.SKIPPED))
                continue
            started = time.perf_counter()
            try:
                response = await client.get(entry.launch_url, headers={"Accept": "text/html,*/*"})
                duration = (time.perf_counter() - started) * 1000
                code = response.status_code
                if code == 429:
                    state = ProbeState.RATE_LIMITED
                    halted = True
                elif code == 403:
                    state = ProbeState.BLOCKED
                    halted = True
                elif code == 401:
                    state = ProbeState.AUTH_REQUIRED
                elif 200 <= code < 400:
                    state = ProbeState.REACHABLE
                else:
                    state = ProbeState.SERVER_ERROR
                results.append(LoginProbe(entry.identifier, state, code, duration))
            except httpx.RequestError:
                results.append(LoginProbe(
                    entry.identifier, ProbeState.CONNECTION_ERROR,
                    elapsed_ms=(time.perf_counter() - started) * 1000
                ))
    return tuple(results)


class CatalogState(str, Enum):
    VERIFIED_OK = "verified_ok"
    UNAVAILABLE = "unavailable"
    AUTH_EXPIRED = "auth_expired"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class CatalogObservation:
    """Evidence supplied by an adapter after actual identity + catalog checks.

    observed_at uses time.monotonic(), not a server timestamp. This data is
    separate from LoginProbe; HTTP 200 alone cannot create VERIFIED_OK.
    """
    entry_id: str
    account_scope: str
    state: CatalogState
    observed_at: float
    identity_verified: bool = False
    catalog_verified: bool = False
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        if (not self.entry_id or not self.account_scope
                or not isinstance(self.state, CatalogState)
                or not math.isfinite(self.observed_at)
                or (self.latency_ms is not None
                    and (not math.isfinite(self.latency_ms) or self.latency_ms < 0))):
            raise ValueError("Invalid read-only catalog observation")
        if self.state == CatalogState.VERIFIED_OK and not (
            self.identity_verified is True and self.catalog_verified is True
        ):
            raise ValueError("A successful catalog observation needs both proofs")


def choose_readonly_catalog_route(
    observations: Sequence[CatalogObservation],
    *,
    account_scope: str,
    current_entry_id: str | None = None,
    now: float | None = None,
    max_age_seconds: float = 120.0,
    trusted_registry: Iterable[OfficialEntry] = OFFICIAL_ENTRIES,
) -> str | None:
    """Plan read-only routing only. Never switches a live HTTP client or writes.

    All candidates must be trusted WEB_DIRECT catalog entries, fresh, verified
    for the same account, and not rate-limited. Prefer the existing healthy
    route to avoid oscillation. Any current authentication/rate-limit failure
    stops routing; a rate limit anywhere in the same account stops the plan.
    """
    if not account_scope or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
        raise ValueError("Invalid account scope or observation lifetime")
    at = time.monotonic() if now is None else now
    if not math.isfinite(at):
        raise ValueError("Invalid clock")
    allowed = {
        entry.identifier for entry in trusted_registry
        if entry.kind is EntryKind.WEB_DIRECT and entry.catalog_candidate is True
    }
    latest: dict[str, CatalogObservation] = {}
    for sample in observations:
        if not isinstance(sample, CatalogObservation):
            raise TypeError("Expected verified catalog observations")
        if sample.account_scope == account_scope and sample.entry_id in allowed:
            latest[sample.entry_id] = sample
    fresh = {
        key: sample for key, sample in latest.items()
        if 0 <= at - sample.observed_at <= max_age_seconds
    }
    current = fresh.get(current_entry_id) if current_entry_id else None
    if current and current.state in (CatalogState.AUTH_EXPIRED, CatalogState.RATE_LIMITED):
        return None
    if any(s.state == CatalogState.RATE_LIMITED for s in fresh.values()):
        return None
    usable = {
        key: sample for key, sample in fresh.items()
        if sample.state is CatalogState.VERIFIED_OK
        and sample.identity_verified is True
        and sample.catalog_verified is True
        and sample.latency_ms is not None
    }
    if current_entry_id in usable:
        return current_entry_id
    if not usable:
        return None
    return min(usable, key=lambda key: (usable[key].latency_ms, key))
