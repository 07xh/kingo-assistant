"""Rate enforcement for :class:`~core.rush_profile.RushProfile`.

``GlobalRequestBudget`` answers "may this machine send one more request inside
the declared server window", and it is deliberately serial: one OS lease, one
database, one holder. That is correct for a shared production account and
useless for a burst, where the whole point is several sends alive at once.

This module is the burst side of the same question, without touching the
database: an in-process in-flight semaphore (machine-wide), one pacer per
account (interval + rolling window), and a per-account carry for a server's
``Retry-After``. It never relaxes the server_policy window -- if both exist,
the stricter one wins because every send still passes through
``GlobalRequestBudget.request()`` inside the transport.

Why per account: two identities must be paced independently. A shared pacer
would let one account's burst delay another's, which is exactly the fan-out
failure the planner refuses to configure.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import math
import time
from typing import AsyncIterator, Awaitable, Callable, Iterable

from core.rush_profile import RushProfile


class BudgetError(ValueError):
    """A burst budget was asked to do something it cannot bound."""


@dataclass(slots=True)
class PacerStats:
    sent: int = 0
    waits: int = 0
    slept_seconds: float = 0.0
    longest_wait: float = 0.0

    def as_dict(self) -> dict:
        return {
            "sent": self.sent,
            "waits": self.waits,
            "slept_seconds": round(self.slept_seconds, 3),
            "longest_wait": round(self.longest_wait, 3),
        }


class AccountPacer:
    """Interval + rolling-window gate for exactly one account.

    The lock is held across the wait on purpose: one account must not race
    itself into two sends 3 ms apart while the pacer is asleep. Different
    accounts hold different pacers, so cross-account work still overlaps.
    """

    def __init__(self, account: str, profile: RushProfile, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        if not isinstance(account, str) or not account.strip():
            raise BudgetError("账号名不能为空")
        self.account = account
        self.profile = profile
        self._clock = clock
        self._sleep = sleeper
        self._lock = asyncio.Lock()
        self._stamps: deque[float] = deque()
        self._next_at = 0.0
        self.stats = PacerStats()

    def _expire(self, now: float) -> None:
        horizon = now - self.profile.window_seconds
        while self._stamps and self._stamps[0] <= horizon:
            self._stamps.popleft()

    def _compute_wait(self, now: float) -> float:
        wait = 0.0
        if now < self._next_at:
            wait = self._next_at - now
        self._expire(now)
        if len(self._stamps) >= self.profile.window_requests:
            # The window frees up when its oldest send leaves the horizon.
            unlock_at = self._stamps[0] + self.profile.window_seconds
            wait = max(wait, unlock_at - now)
        return max(0.0, wait)

    async def acquire(self) -> float:
        """Wait until one send is allowed; returns the seconds actually slept."""
        slept = 0.0
        async with self._lock:
            while True:
                now = self._clock()
                wait = self._compute_wait(now)
                if wait <= 0:
                    self._stamps.append(now)
                    self._next_at = now + self.profile.effective_interval
                    self.stats.sent += 1
                    self.stats.slept_seconds += slept
                    return slept
                # ``+ 0.001`` keeps float rounding from re-entering the gate in
                # the same instant and spinning a microsecond loop.
                pause = wait + 0.001
                self.stats.waits += 1
                self.stats.longest_wait = max(self.stats.longest_wait, pause)
                await self._sleep(pause)
                slept += pause

    def impose_retry_after(self, seconds: float) -> None:
        """Remember a server-issued cooldown without dropping queued work."""
        if type(seconds) not in (int, float) or isinstance(seconds, bool):
            raise BudgetError("Retry-After 必须是数字")
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            raise BudgetError("Retry-After 不能为负或无穷")
        value = min(value, self.profile.retry_after_cap)
        now = self._clock()
        self._next_at = max(self._next_at, now + value)

    def reset(self) -> None:
        self._stamps.clear()
        self._next_at = 0.0


class RushBudget:
    """Machine-wide in-flight cap plus one pacer per account."""

    def __init__(self, profile: RushProfile, accounts: Iterable[str], *,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        names = [str(name) for name in accounts]
        if not names:
            raise BudgetError("突发预算至少需要一个账号")
        if len(set(names)) != len(names):
            raise BudgetError("账号名重复")
        self.profile = profile
        self._clock = clock
        self._sleep = sleeper
        self._inflight = asyncio.Semaphore(profile.concurrency)
        self._pacers = {
            name: AccountPacer(name, profile, clock=clock, sleeper=sleeper)
            for name in names
        }
        #: Fair share: no account may hold every in-flight slot for itself.
        share = max(1, profile.concurrency // len(names))
        self.per_account_inflight = share
        self._account_slots = {
            name: asyncio.Semaphore(share) for name in names
        }
        self.total_sent = 0
        self.peak_inflight = 0
        self._current_inflight = 0

    @property
    def accounts(self) -> tuple[str, ...]:
        return tuple(self._pacers)

    def pacer(self, account: str) -> AccountPacer:
        try:
            return self._pacers[account]
        except KeyError as exc:
            raise BudgetError(f"未知账号 {account}") from exc

    def impose_retry_after(self, account: str, seconds: float) -> None:
        self.pacer(account).impose_retry_after(seconds)

    @asynccontextmanager
    async def slot(self, account: str) -> AsyncIterator[None]:
        """One send for ``account``: fair share, pacing, then the in-flight cap.

        Pacing happens *before* the machine-wide slot is taken. Holding a slot
        while sleeping would let one account's 30 s cooldown occupy a share of
        the burst budget that another account could have used.
        """
        pacer = self.pacer(account)
        per_account = self._account_slots[account]
        async with per_account:
            await pacer.acquire()
            async with self._inflight:
                self._current_inflight += 1
                self.peak_inflight = max(self.peak_inflight, self._current_inflight)
                try:
                    self.total_sent += 1
                    yield
                finally:
                    self._current_inflight -= 1

    def snapshot(self) -> dict:
        return {
            "profile": self.profile.as_dict(),
            "total_sent": self.total_sent,
            "peak_inflight": self.peak_inflight,
            "per_account_inflight": self.per_account_inflight,
            "accounts": {name: pacer.stats.as_dict()
                         for name, pacer in self._pacers.items()},
        }
