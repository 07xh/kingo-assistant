"""Concurrent multi-account execution for the write phase.

The planner answers "is this fan-out safe to attempt"; this module attempts it.
One task per account, each with its own session, its own pacer and its own
journal, all sharing one machine-wide in-flight cap. Nothing here fabricates a
session: an account without a session factory is reported as ``no_session``
and never touched, so a missing credential can never look like a lost race.

Failure handling is per target, not per run:

======================  ===========================================
kind                    action
======================  ===========================================
``duplicate``           stop retrying this target, count as held
``throttled``           honour ``Retry-After``, then back off
``challenge``           halt this account immediately (CAPTCHA/slider)
``transient``           exponential backoff with jitter
``fatal``               stop this target, keep the account alive
======================  ===========================================

A challenge is never retried: retrying it burns the attempt counter that
locks the account. A run-wide deadline bounds the whole burst, and every
session is closed even when the run is cancelled mid-flight.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import inspect
import math
import random
import time
from typing import Any, Awaitable, Callable, Iterable, Mapping, Protocol, Sequence

from core.multi_account_plan import AccountPlan, AccountTarget
from core.rush_budget import BudgetError, RushBudget
from core.rush_profile import RushProfile


class RunError(ValueError):
    """The runner was configured in a way that cannot produce a valid run."""


class FailureKind(str, Enum):
    DUPLICATE = "duplicate"
    THROTTLED = "throttled"
    CHALLENGE = "challenge"
    TRANSIENT = "transient"
    FATAL = "fatal"


#: Markers are matched case-insensitively against ``str(exc)`` plus any
#: ``reason``/``message`` attribute. A school that returns a 200 with Chinese
#: text is the normal case, so status code alone is not enough.
_DUPLICATE_MARKERS = ("已选", "已经选", "重复选课", "duplicate", "already enrolled",
                      "已存在", "冲突")
_CHALLENGE_MARKERS = ("验证码", "滑块", "captcha", "challenge", "短信验证",
                      "请完成安全验证", "人机")
_THROTTLE_MARKERS = ("429", "too many requests", "限流", "过于频繁", "操作频繁",
                     "请求过快", "retry-after", "rate limit")
_TRANSIENT_MARKERS = ("timeout", "timed out", "超时", "连接", "connection",
                      "temporarily", "502", "503", "504", "reset", "网络")


def _text(exc: BaseException) -> str:
    parts = [str(exc), type(exc).__name__]
    for attr in ("reason", "message", "detail", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, int):
            parts.append(str(value))
    return " ".join(parts).lower()


def classify_failure(exc: BaseException) -> tuple[FailureKind, float | None]:
    """Map a submit failure onto the retry policy.

    Order matters: a 429 body often also contains the word "重复" in a help
    link, and a challenge page always contains "验证码". Challenge is checked
    first because it is the only kind where retrying is actively harmful.
    """
    text = _text(exc)
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    retry_after = None
    raw_retry = getattr(exc, "retry_after", None)
    if isinstance(raw_retry, (int, float)) and not isinstance(raw_retry, bool):
        value = float(raw_retry)
        if math.isfinite(value) and value >= 0:
            retry_after = value
    if status == 429 and retry_after is None:
        retry_after = 1.0
    if any(marker in text for marker in _CHALLENGE_MARKERS):
        return FailureKind.CHALLENGE, None
    if any(marker in text for marker in _DUPLICATE_MARKERS):
        return FailureKind.DUPLICATE, None
    if status == 429 or any(marker in text for marker in _THROTTLE_MARKERS):
        return FailureKind.THROTTLED, retry_after
    if status is not None and 500 <= status < 600:
        return FailureKind.TRANSIENT, retry_after
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return FailureKind.TRANSIENT, retry_after
    return FailureKind.FATAL, retry_after


class AccountSession(Protocol):
    """One identity's write path. Implemented by the NCWU gateway adapter."""

    async def submit(self, target: AccountTarget) -> Any: ...

    async def aclose(self) -> None: ...


SessionFactory = Callable[[AccountPlan], "AccountSession | Awaitable[AccountSession]"]


@dataclass(slots=True)
class TargetOutcome:
    account: str
    course_id: str
    class_id: str
    status: str
    attempts: int = 0
    detail: str = ""
    receipt: Any = None

    def as_dict(self) -> dict:
        return {
            "account": self.account,
            "course_id": self.course_id,
            "class_id": self.class_id,
            "status": self.status,
            "attempts": self.attempts,
            "detail": self.detail,
        }


@dataclass(slots=True)
class AccountOutcome:
    name: str
    status: str
    halted_reason: str = ""
    targets: list[TargetOutcome] = field(default_factory=list)
    started_after: float = 0.0

    @property
    def submitted(self) -> int:
        return sum(1 for t in self.targets if t.status == "submitted")

    @property
    def held(self) -> int:
        return sum(1 for t in self.targets if t.status == "duplicate")

    @property
    def failed(self) -> int:
        return sum(1 for t in self.targets
                   if t.status in ("failed", "halted", "skipped_deadline",
                                   "skipped_halted"))

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "halted_reason": self.halted_reason,
            "started_after": round(self.started_after, 3),
            "submitted": self.submitted,
            "held": self.held,
            "failed": self.failed,
            "targets": [t.as_dict() for t in self.targets],
        }


@dataclass(slots=True)
class RunReport:
    accounts: list[AccountOutcome]
    elapsed: float
    profile: RushProfile
    budget: dict = field(default_factory=dict)

    @property
    def submitted(self) -> int:
        return sum(a.submitted for a in self.accounts)

    @property
    def held(self) -> int:
        return sum(a.held for a in self.accounts)

    @property
    def failed(self) -> int:
        return sum(a.failed for a in self.accounts)

    @property
    def halted(self) -> list[str]:
        return [a.name for a in self.accounts if a.status == "halted"]

    def as_dict(self) -> dict:
        return {
            "elapsed": round(self.elapsed, 3),
            "profile": self.profile.as_dict(),
            "totals": {
                "submitted": self.submitted,
                "held": self.held,
                "failed": self.failed,
                "halted_accounts": self.halted,
            },
            "budget": self.budget,
            "accounts": [a.as_dict() for a in self.accounts],
        }


def _backoff(profile: RushProfile, attempt: int, jitter: float) -> float:
    raw = profile.backoff_base * (2 ** min(attempt - 1, 16))
    capped = min(raw, profile.backoff_cap)
    spread = capped * profile.backoff_jitter * jitter
    return max(0.0, capped + spread)


class MultiAccountRunner:
    """Fan one burst across several independently paced accounts."""

    def __init__(self, *, plans: Sequence[AccountPlan],
                 session_factory: SessionFactory,
                 profile: RushProfile,
                 budget: RushBudget | None = None,
                 classifier: Callable[[BaseException], tuple[FailureKind, float | None]] = classify_failure,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 random_source: random.Random | None = None,
                 on_event: Callable[[Mapping[str, Any]], None] | None = None,
                 deadline_seconds: float | None = None) -> None:
        plans = list(plans)
        if not plans:
            raise RunError("至少需要一个账号计划")
        names = [plan.name for plan in plans]
        if len(set(names)) != len(names):
            raise RunError("账号名称重复")
        databases = [str(plan.database).casefold() for plan in plans]
        if len(set(databases)) != len(databases):
            raise RunError("两个账号共用预算数据库，会被串行化")
        if not callable(session_factory):
            raise RunError("缺少会话工厂")
        if not isinstance(profile, RushProfile):
            raise RunError("需要 RushProfile")
        self.plans = plans
        self.session_factory = session_factory
        self.profile = profile
        self.budget = budget or RushBudget(profile, names, clock=clock, sleeper=sleeper)
        self.classifier = classifier
        self.clock = clock
        self.sleeper = sleeper
        self._random = random_source or random.Random(0xC0FFEE)
        self.on_event = on_event
        self.deadline_seconds = (profile.deadline_seconds
                                 if deadline_seconds is None else deadline_seconds)
        if not 0 < self.deadline_seconds <= 3600:
            raise RunError("突发截止时间必须在 0–3600 秒之间")
        self._deadline_at = 0.0

    # ---------------------------------------------------------------- helpers
    def _emit(self, **payload: Any) -> None:
        if self.on_event is not None:
            self.on_event(payload)

    def _expired(self) -> bool:
        return self.clock() >= self._deadline_at

    async def _open(self, plan: AccountPlan) -> AccountSession:
        session = self.session_factory(plan)
        if inspect.isawaitable(session):
            session = await session
        if not callable(getattr(session, "submit", None)):
            raise RunError(f"账号 {plan.name} 的会话缺少 submit()")
        return session

    async def _close(self, session: AccountSession) -> None:
        closer = getattr(session, "aclose", None)
        if callable(closer):
            try:
                await closer()
            except Exception:  # closing must never mask the run result
                pass

    # ------------------------------------------------------------------ submit
    async def _attempt(self, plan: AccountPlan, session: AccountSession,
                       target: AccountTarget) -> tuple[TargetOutcome, str]:
        """Drive one target to a terminal outcome.

        Returns the outcome plus the halt reason (empty when the account stays
        usable). A ``duplicate`` counts as held, not as a failure: the seat is
        already ours, which for grabbing is the good ending.
        """
        attempts = 0
        consecutive = 0
        while attempts < self.profile.max_attempts:
            if self._expired():
                return TargetOutcome(plan.name, target.course_id, target.class_id,
                                     "skipped_deadline", attempts,
                                     "超出突发截止时间"), ""
            attempts += 1
            try:
                async with self.budget.slot(plan.name):
                    receipt = await session.submit(target)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                kind, retry_after = self.classifier(exc)
                detail = f"{type(exc).__name__}: {str(exc)[:160]}"
                if kind is FailureKind.DUPLICATE:
                    self._emit(event="held", account=plan.name, target=target.key,
                               attempts=attempts, detail=detail)
                    return TargetOutcome(plan.name, target.course_id,
                                         target.class_id, "duplicate", attempts,
                                         detail), ""
                if kind is FailureKind.CHALLENGE:
                    self._emit(event="halt", account=plan.name, target=target.key,
                               attempts=attempts, detail=detail)
                    return TargetOutcome(plan.name, target.course_id,
                                         target.class_id, "halted", attempts,
                                         detail), f"触发人机校验，停止该账号：{detail}"
                if kind is FailureKind.FATAL:
                    self._emit(event="fatal", account=plan.name, target=target.key,
                               attempts=attempts, detail=detail)
                    return TargetOutcome(plan.name, target.course_id,
                                         target.class_id, "failed", attempts,
                                         detail), ""
                consecutive += 1
                if consecutive >= self.profile.circuit_breaker:
                    self._emit(event="breaker", account=plan.name,
                               target=target.key, attempts=attempts, detail=detail)
                    return TargetOutcome(
                        plan.name, target.course_id, target.class_id, "failed",
                        attempts, detail), (
                        f"连续 {consecutive} 次限流/网络失败，熔断：{detail}")
                if retry_after is not None:
                    # The pacer carries the cooldown; sleeping here as well
                    # would double the wait for every other target.
                    self.budget.impose_retry_after(plan.name, retry_after)
                else:
                    await self.sleeper(_backoff(self.profile, attempts,
                                                self._random.random()))
                self._emit(event="retry", account=plan.name, target=target.key,
                           attempts=attempts, kind=kind.value, detail=detail)
                continue
            else:
                self._emit(event="submitted", account=plan.name,
                           target=target.key, attempts=attempts)
                return TargetOutcome(plan.name, target.course_id, target.class_id,
                                     "submitted", attempts, "已提交",
                                     receipt=receipt), ""
        return TargetOutcome(plan.name, target.course_id, target.class_id, "failed",
                             attempts, "达到最大尝试次数"), ""

    # ----------------------------------------------------------------- account
    async def _drive(self, plan: AccountPlan, index: int) -> AccountOutcome:
        started = self.clock()
        delay = index * self.profile.stagger_ms / 1000.0
        if delay > 0:
            await self.sleeper(delay)
        outcome = AccountOutcome(plan.name, "pending", started_after=delay)
        try:
            session = await self._open(plan)
        except Exception as exc:
            outcome.status = "no_session"
            outcome.halted_reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            for target in plan.targets:
                outcome.targets.append(TargetOutcome(
                    plan.name, target.course_id, target.class_id, "failed", 0,
                    outcome.halted_reason))
            self._emit(event="account_error", account=plan.name,
                       detail=outcome.halted_reason)
            return outcome
        try:
            for index, target in enumerate(plan.targets):
                result, halt = await self._attempt(plan, session, target)
                outcome.targets.append(result)
                if not halt:
                    continue
                outcome.status = "halted"
                outcome.halted_reason = halt
                for rest in plan.targets[index + 1:]:
                    outcome.targets.append(TargetOutcome(
                        plan.name, rest.course_id, rest.class_id,
                        "skipped_halted", 0, "账号已熔断"))
                break
            else:
                outcome.status = self._classify_account(outcome)
            self._emit(event="account_done", account=plan.name,
                       status=outcome.status)
            return outcome
        finally:
            await self._close(session)

    @staticmethod
    def _classify_account(outcome: AccountOutcome) -> str:
        ok = outcome.submitted + outcome.held
        if ok == len(outcome.targets):
            return "submitted" if outcome.submitted else "already_held"
        if ok == 0:
            return "failed"
        return "partial"

    # --------------------------------------------------------------------- run
    async def run(self) -> RunReport:
        started = self.clock()
        self._deadline_at = started + self.deadline_seconds
        self._emit(event="run_start", accounts=[p.name for p in self.plans],
                   profile=self.profile.as_dict(), deadline=self.deadline_seconds)
        tasks = [asyncio.create_task(self._drive(plan, index))
                 for index, plan in enumerate(self.plans)]
        try:
            outcomes = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        elapsed = self.clock() - started
        report = RunReport(accounts=list(outcomes), elapsed=elapsed,
                           profile=self.profile, budget=self.budget.snapshot())
        self._emit(event="run_done", elapsed=round(elapsed, 3),
                   submitted=report.submitted, held=report.held,
                   failed=report.failed)
        return report


def run_summary(report: RunReport) -> str:
    """One-screen Chinese summary; used by the CLI and by log lines."""
    lines = [
        f"耗时 {report.elapsed:.2f} s｜提交 {report.submitted}｜已持有 {report.held}"
        f"｜未成 {report.failed}"
    ]
    if report.halted:
        lines.append(f"熔断账号：{', '.join(report.halted)}")
    for account in report.accounts:
        lines.append(
            f"  {account.name}: {account.status}"
            f"（提交 {account.submitted}/持有 {account.held}/失败 {account.failed}）"
            + (f" — {account.halted_reason}" if account.halted_reason else ""))
    sent = report.budget.get("total_sent")
    peak = report.budget.get("peak_inflight")
    if sent is not None and report.elapsed > 0:
        lines.append(
            f"实际发送 {sent} 次，峰值并发 {peak}，"
            f"有效吞吐 {sent / report.elapsed:.2f} 次/秒")
    return "\n".join(lines)
