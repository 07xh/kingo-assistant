#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V4 多账号编排器 —— 每账号独立预算库、独立出口、独立电路熔断。

为什么不是"提高并发数"：仓库 bench 实测 8 并发把同一课程的提交成功率从
1.00 打到 0.125。真正值得并行的是**不同账号抢不同目标**，所以这里做的是
账号级并行，账号内部仍是串行（每账号 Semaphore(1)）。

组件：

    SlidingBudget       每账号一个 SQLite 滑动窗口（跨进程安全，和仓库
                        GlobalRequestBudget 语义一致，但独立 db 路径）
    ProxyPool           出口轮换，支持按失败次数降权
    CircuitBreaker      N 次鉴权失败即停用该账号，不影响其他账号
    MultiAccountOrchestrator
                        交错启动 + 每账号独立预算 + 结果聚合

探测动作不在这里猜：调用方注入 ``probe(Attempt) -> ProbeOutcome``，
所以既能接仓库现有 engine，也能接自己的客户端。全部 stdlib。

用法::

    python v4_multiaccount.py selftest
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional


# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------

class BudgetError(RuntimeError):
    """窗口参数非法或时钟回退。"""


@dataclass(frozen=True)
class Window:
    seconds: float
    max_requests: int

    def __post_init__(self) -> None:
        if (isinstance(self.seconds, bool)
                or not isinstance(self.seconds, (int, float))
                or not math.isfinite(self.seconds) or self.seconds <= 0):
            raise BudgetError("窗口长度必须是有限正数")
        if type(self.max_requests) is not int or self.max_requests <= 0:
            raise BudgetError("窗口请求数必须是正整数")


class SlidingBudget:
    """单账号独立预算：SQLite 滑动日志 + Retry-After 全局退避。

    每个账号用**不同**的 db 文件，所以跨账号互不阻塞；同一账号多进程共用
    一个 db 时仍是原子的。
    """

    def __init__(self, database: Path | str, windows: Iterable[Window]) -> None:
        self.windows = tuple(windows)
        if not self.windows:
            raise BudgetError("至少需要一个窗口")
        self.database = Path(database)
        if str(self.database) == ":memory:":
            raise BudgetError("预算库必须是可共享的文件路径")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database), timeout=5, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE IF NOT EXISTS requests (at REAL NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS requests_at ON requests(at)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value REAL NOT NULL)")
        finally:
            conn.close()

    def _reserve(self) -> float:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            last = conn.execute("SELECT value FROM meta WHERE key='last_seen'").fetchone()
            if last is not None and now + 0.001 < last[0]:
                raise BudgetError("检测到系统时钟回退，停止请求")
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('last_seen',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
            block = conn.execute("SELECT value FROM meta WHERE key='backoff_until'").fetchone()
            wait = max(0.0, block[0] - now) if block else 0.0
            longest = max(w.seconds for w in self.windows)
            conn.execute("DELETE FROM requests WHERE at <= ?", (now - longest,))
            stamps = [row[0] for row in conn.execute(
                "SELECT at FROM requests WHERE at > ? ORDER BY at", (now - longest,))]
            for window in self.windows:
                active = [s for s in stamps if s > now - window.seconds]
                if len(active) >= window.max_requests:
                    unlock = active[len(active) - window.max_requests] + window.seconds
                    wait = max(wait, unlock - now + 0.002)
            if wait <= 0:
                conn.execute("INSERT INTO requests(at) VALUES (?)", (now,))
            conn.commit()
            return max(0.0, wait)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def impose_retry_after(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise BudgetError("Retry-After 必须是数字")
        if not math.isfinite(seconds) or seconds < 0:
            raise BudgetError("Retry-After 非法")
        conn = self._connect()
        try:
            now = time.time()
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('backoff_until',?) "
                "ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)",
                (now + float(seconds),))
            conn.commit()
        finally:
            conn.close()

    async def acquire(self) -> float:
        """阻塞直到拿到一个配额，返回实际等待秒数。"""
        waited = 0.0
        while True:
            async with self._lock:
                wait = await asyncio.to_thread(self._reserve)
            if wait <= 0:
                return waited
            waited += wait
            await asyncio.sleep(min(wait, 5.0))

    def stats(self) -> dict:
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            span = conn.execute("SELECT MIN(at),MAX(at) FROM requests").fetchone()
        finally:
            conn.close()
        return {"db": str(self.database), "logged": total,
                "first_at": span[0], "last_at": span[1],
                "windows": [[w.seconds, w.max_requests] for w in self.windows]}


# ---------------------------------------------------------------------------
# 出口与熔断
# ---------------------------------------------------------------------------

class ProxyPool:
    """出口轮换：按失败计数降权，全失败时回落到直连。"""

    def __init__(self, proxies: Iterable[str] = ()) -> None:
        self.proxies = [p for p in proxies if p]
        self.failures: dict[str, int] = {p: 0 for p in self.proxies}
        self.cursor = 0
        self.direct = True

    def next(self) -> Optional[str]:
        if not self.proxies:
            return None
        ordered = sorted(self.proxies, key=lambda p: self.failures[p])
        best = ordered[0]
        if self.failures[best] >= 3:
            return None if self.direct else best
        self.cursor = (self.proxies.index(best) + 1) % len(self.proxies)
        return best

    def penalize(self, proxy: Optional[str]) -> None:
        if proxy in self.failures:
            self.failures[proxy] += 1

    def reward(self, proxy: Optional[str]) -> None:
        if proxy in self.failures and self.failures[proxy] > 0:
            self.failures[proxy] -= 1

    def report(self) -> dict:
        return {"proxies": len(self.proxies), "failures": dict(self.failures),
                "direct_fallback": self.direct}


class CircuitBreaker:
    """连续 N 次鉴权失败即熔断该账号，冷却后半开探测。"""

    def __init__(self, threshold: int = 3, cooldown_s: float = 300.0) -> None:
        if threshold < 1:
            raise ValueError("threshold 必须 >= 1")
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.failures = 0
        self.opened_at: float | None = None
        self.trips = 0

    @property
    def open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.time() - self.opened_at >= self.cooldown:
            self.opened_at = None
            self.failures = self.threshold - 1     # 半开：只放一次
            return False
        return True

    def record(self, auth_failed: bool) -> None:
        if auth_failed:
            self.failures += 1
            if self.failures >= self.threshold and self.opened_at is None:
                self.opened_at = time.time()
                self.trips += 1
        else:
            self.failures = 0

    def report(self) -> dict:
        return {"open": self.open, "failures": self.failures,
                "trips": self.trips, "cooldown_s": self.cooldown}


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

@dataclass
class AccountSpec:
    name: str
    targets: list[dict]
    budget: SlidingBudget
    proxy: Optional[str] = None
    cookie: Optional[str] = None
    stagger_ms: int = 0
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("账号名不能为空")
        if not isinstance(self.targets, list):
            raise ValueError("targets 必须是列表")


@dataclass
class Attempt:
    account: AccountSpec
    target: dict
    proxy: Optional[str]
    round_index: int
    started_at: float


@dataclass
class ProbeOutcome:
    """一次探测的真实结果，由调用方注入的 probe 返回。"""

    offers: list[dict] = field(default_factory=list)
    submitted: bool = False
    confirmed: bool = False
    auth_failed: bool = False
    retry_after: float | None = None
    error: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error and not self.auth_failed


@dataclass
class RoundReport:
    account: str
    target: str
    took_ms: float
    waited_ms: float
    confirmed: bool = False
    submitted: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {"account": self.account, "target": self.target,
                "took_ms": round(self.took_ms, 3), "waited_ms": round(self.waited_ms, 3),
                "confirmed": self.confirmed, "submitted": self.submitted,
                "error": self.error}


Probe = Callable[[Attempt], Awaitable[ProbeOutcome]]


def target_key(target: dict) -> str:
    return f"{target.get('course_id', '')}/{target.get('class_id', '')}"


class MultiAccountOrchestrator:
    """账号级并行、账号内串行、预算独立、失败隔离。"""

    def __init__(self, accounts: list[AccountSpec], probe: Probe, *,
                 proxies: Optional[ProxyPool] = None,
                 jitter_ms: int = 200) -> None:
        if not accounts:
            raise ValueError("至少需要一个账号")
        self.accounts = accounts
        self.probe = probe
        self.proxies = proxies or ProxyPool()
        self.jitter_ms = max(0, int(jitter_ms))
        self.reports: list[RoundReport] = []
        self._done: set[str] = set()

    def _enabled_targets(self, account: AccountSpec) -> list[dict]:
        out = []
        for target in account.targets:
            key = f"{account.name}\x1f{target_key(target)}"
            if key in self._done:
                continue
            if target.get("enabled", True):
                out.append(target)
        return out

    def _mark_done(self, account: AccountSpec, target: dict) -> None:
        self._done.add(f"{account.name}\x1f{target_key(target)}")

    async def _run_account(self, account: AccountSpec, round_index: int,
                           stagger_s: float) -> list[RoundReport]:
        await asyncio.sleep(stagger_s)
        local: list[RoundReport] = []
        for target in self._enabled_targets(account):
            if account.breaker.open:
                local.append(RoundReport(account.name, target_key(target), 0.0, 0.0,
                                         error="circuit_open"))
                continue
            proxy = self.proxies.next() or account.proxy
            waited = await account.budget.acquire()
            attempt = Attempt(account=account, target=target, proxy=proxy,
                              round_index=round_index, started_at=time.time())
            started = time.perf_counter()
            try:
                outcome = await self.probe(attempt)
            except Exception as exc:                     # noqa: BLE001
                outcome = ProbeOutcome(error=f"{type(exc).__name__}: {exc}")
            took = (time.perf_counter() - started) * 1000.0

            if outcome.retry_after is not None:
                account.budget.impose_retry_after(outcome.retry_after)
            account.breaker.record(outcome.auth_failed)
            if outcome.error and not outcome.auth_failed:
                self.proxies.penalize(proxy)
            else:
                self.proxies.reward(proxy)

            local.append(RoundReport(
                account.name, target_key(target), took, waited * 1000.0,
                confirmed=outcome.confirmed, submitted=outcome.submitted,
                error=outcome.error))
            if outcome.confirmed:
                self._mark_done(account, target)
        return local

    async def run(self, rounds: int = 1, *, deadline_s: float | None = None) -> dict:
        started = time.perf_counter()
        for round_index in range(max(1, rounds)):
            tasks = []
            for index, account in enumerate(self.accounts):
                stagger = max(account.stagger_ms, self.jitter_ms * index) / 1000.0
                tasks.append(self._run_account(account, round_index, stagger))
            if deadline_s is not None:
                remaining = deadline_s - (time.perf_counter() - started)
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    [asyncio.create_task(t) for t in tasks], timeout=remaining)
                for task in pending:
                    task.cancel()
                for task in done:
                    self.reports.extend(task.result())
            else:
                for result in await asyncio.gather(*tasks):
                    self.reports.extend(result)
        return self.summary()

    def summary(self) -> dict:
        confirmed = [r for r in self.reports if r.confirmed]
        return {
            "accounts": len(self.accounts),
            "attempts": len(self.reports),
            "confirmed": len(confirmed),
            "submitted": sum(1 for r in self.reports if r.submitted),
            "errors": sum(1 for r in self.reports if r.error),
            "budgets": {a.name: a.budget.stats() for a in self.accounts},
            "breakers": {a.name: a.breaker.report() for a in self.accounts},
            "proxies": self.proxies.report(),
            "reports": [r.as_dict() for r in self.reports],
        }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import tempfile

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            # --- 预算：0.05s 窗口内只允许 1 次 ---
            budget = SlidingBudget(base / "acctA.db", [Window(0.05, 1)])
            stamps = []
            for _ in range(3):
                await budget.acquire()
                stamps.append(time.perf_counter())
            gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
            assert all(g >= 0.045 for g in gaps), gaps
            # 窗口只有 0.05s，过期行会被清理，所以只断言仍在库里的记录可读
            assert budget.stats()["logged"] >= 1

            # --- 两个账号独立预算，互不阻塞 ---
            budget_b = SlidingBudget(base / "acctB.db", [Window(5.0, 1)])
            started = time.perf_counter()
            await asyncio.gather(budget.acquire(), budget_b.acquire())
            assert time.perf_counter() - started < 1.0, "独立账号不应互相阻塞"

            # --- 编排：A 账号第 2 轮抢到，B 账号连续鉴权失败熔断 ---
            calls = {"A": 0, "B": 0}

            async def probe(attempt: Attempt) -> ProbeOutcome:
                name = attempt.account.name
                calls[name] += 1
                if name == "A":
                    if calls[name] < 2:
                        return ProbeOutcome(offers=[{"course_id": "K1", "class_id": "C1",
                                                     "remain": 0}])
                    return ProbeOutcome(offers=[{"course_id": "K1", "class_id": "C1",
                                                 "remain": 3}],
                                        submitted=True, confirmed=True,
                                        detail={"latency_ms": 12})
                return ProbeOutcome(auth_failed=True, error="401")

            acc_a = AccountSpec(
                name="A", targets=[{"course_id": "K1", "class_id": "C1"}],
                budget=SlidingBudget(base / "A.db", [Window(0.01, 5)]))
            acc_b = AccountSpec(
                name="B", targets=[{"course_id": "K2", "class_id": "C2"}],
                budget=SlidingBudget(base / "B.db", [Window(0.01, 5)]),
                breaker=CircuitBreaker(threshold=2, cooldown_s=60))
            orch = MultiAccountOrchestrator([acc_a, acc_b], probe, jitter_ms=0)
            summary = await orch.run(rounds=3)

            assert summary["confirmed"] == 1, summary
            assert summary["breakers"]["B"]["open"] is True, summary["breakers"]
            assert summary["breakers"]["B"]["trips"] == 1
            # A 抢到后不再重复提交
            assert calls["A"] == 2, calls
            assert calls["B"] == 2, calls

            # --- Retry-After 落库 ---
            budget.impose_retry_after(0.05)
            t0 = time.perf_counter()
            await budget.acquire()
            assert time.perf_counter() - t0 >= 0.04

            # --- 代理降权 ---
            pool = ProxyPool(["http://p1", "http://p2"])
            first = pool.next()
            pool.penalize(first)
            pool.penalize(first)
            pool.penalize(first)
            assert pool.next() != first, "失败 3 次的出口应被降权"

            json.dumps(summary, ensure_ascii=False)

    asyncio.run(scenario())
    print("PASS selftest")
    print("  预算: 0.05s 窗口内串行、双账号互不阻塞、Retry-After 生效")
    print("  编排: 抢到后不重复提交、鉴权失败 2 次熔断、出口失败降权")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V4 多账号编排器")
    sub = parser.add_subparsers(dest="command", required=True)
    selftest = sub.add_parser("selftest")
    selftest.set_defaults(func=lambda _args: _selftest())
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
