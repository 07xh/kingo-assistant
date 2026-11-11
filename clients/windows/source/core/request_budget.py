"""Explicit-policy, shared SQLite request budget (standard library only).

All HTTP requests to a protected origin must pass through request().  The
SQLite reservation is atomic across processes using the same database path.
A reservation counts even if the subsequent request is cancelled; this is a
conservative choice. Wall clock changes cannot be fully resolved by client
code, so this limiter is not proof of institutional permission or compliance.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3
import time
from typing import AsyncIterator


class PolicyError(ValueError):
    """Unverified/invalid policy or untrustworthy persisted clock state."""


@dataclass(frozen=True)
class WindowLimit:
    window_seconds: float
    max_requests: int

    def __post_init__(self) -> None:
        if (type(self.window_seconds) not in (int, float)
                or not math.isfinite(self.window_seconds)
                or self.window_seconds <= 0):
            raise PolicyError("窗口长度必须是有限正数")
        if type(self.max_requests) is not int or self.max_requests <= 0:
            raise PolicyError("窗口请求数必须是正整数")


@dataclass(frozen=True)
class RequestPolicy:
    windows: tuple[WindowLimit, ...]
    max_concurrency: int
    source: str
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.windows or len(self.windows) > 8:
            raise PolicyError("必须提供 1–8 个明确窗口")
        if any(not isinstance(w, WindowLimit) for w in self.windows):
            raise PolicyError("窗口类型错误")
        if len(set(w.window_seconds for w in self.windows)) != len(self.windows):
            raise PolicyError("窗口长度重复")
        if type(self.max_concurrency) is not int or self.max_concurrency <= 0:
            raise PolicyError("并发上限无效")
        if not isinstance(self.source, str) or not self.source.strip():
            raise PolicyError("缺少策略来源")


class GlobalRequestBudget:
    """Shared reservations across processes; concurrency is per instance.

    Use a single instance per process for each policy scope. Cross-process
    concurrency additionally requires an OS-level single-run lock or a broker.
    """

    def __init__(self, policy: RequestPolicy, database: Path):
        self.policy = policy
        self.database = Path(database)
        self._concurrency = asyncio.Semaphore(policy.max_concurrency)
        self._lock = asyncio.Lock()
        if self.database.parent.is_symlink() or self.database.is_symlink():
            raise PolicyError("预算数据库不允许符号链接")
        self.database.parent.mkdir(parents=True, exist_ok=True)
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
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL NOT NULL)")
            import hashlib
            import json
            signature = hashlib.sha256(json.dumps(
                [[w.window_seconds, w.max_requests] for w in self.policy.windows],
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            conn.execute("CREATE TABLE IF NOT EXISTS policy (signature TEXT NOT NULL)")
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT signature FROM policy LIMIT 1").fetchone()
            if prior is None:
                conn.execute("INSERT INTO policy(signature) VALUES(?)", (signature,))
            elif prior[0] != signature:
                raise PolicyError("共享预算的窗口策略发生变化，需先核对既有配额")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _transact(self, retry_after: float | None = None) -> float:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            previous = conn.execute("SELECT value FROM metadata WHERE key='last_seen'").fetchone()
            if previous is not None and now + 0.001 < previous[0]:
                raise PolicyError("检测到系统时钟回退：停止请求并等待人工核对")
            if previous is None or now > previous[0]:
                conn.execute("INSERT INTO metadata(key,value) VALUES('last_seen',?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
            if retry_after is not None:
                future = now + retry_after
                conn.execute("INSERT INTO metadata(key,value) VALUES('backoff_until',?) "
                             "ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)",
                             (future,))
                conn.commit()
                return 0.0
            block = conn.execute("SELECT value FROM metadata WHERE key='backoff_until'").fetchone()
            wait = max(0.0, block[0] - now) if block else 0.0
            longest = max(w.window_seconds for w in self.policy.windows)
            conn.execute("DELETE FROM requests WHERE at <= ?", (now - longest,))
            oldest = [row[0] for row in conn.execute(
                "SELECT at FROM requests WHERE at > ? ORDER BY at", (now - longest,)
            )]
            for limit in self.policy.windows:
                active = [stamp for stamp in oldest if stamp > now - limit.window_seconds]
                if len(active) >= limit.max_requests:
                    unlock_at = active[len(active) - limit.max_requests] + limit.window_seconds
                    wait = max(wait, unlock_at - now + 0.002)
            if wait <= 0:
                conn.execute("INSERT INTO requests(at) VALUES (?)", (now,))
            conn.commit()
            return max(0.0, wait)
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    async def impose_retry_after(self, seconds: float) -> None:
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise PolicyError("Retry-After 无效")
        async with self._lock:
            await asyncio.to_thread(self._transact, float(seconds))

    async def _reserve(self) -> None:
        while True:
            async with self._lock:
                wait = await asyncio.to_thread(self._transact)
            if wait == 0:
                return
            await asyncio.sleep(wait)

    @asynccontextmanager
    async def request(self) -> AsyncIterator[None]:
        """Counts one attempted send; concurrency lease spans actual request."""
        if self.policy.verified is not True:
            raise PolicyError("没有独立核实的允许频率策略，拒绝请求")
        async with self._concurrency:
            await self._reserve()
            yield


class SingleFlight:
    """Coalesce identical concurrent reads without cancelling shared work."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[object, asyncio.Task] = {}

    async def run(self, key, factory):
        async with self._lock:
            task = self._tasks.get(key)
            if task is None:
                async def execute():
                    try:
                        return await factory()
                    finally:
                        async with self._lock:
                            if self._tasks.get(key) is asyncio.current_task():
                                self._tasks.pop(key, None)
                task = asyncio.create_task(execute())
                self._tasks[key] = task
        return await asyncio.shield(task)



def read_verified_policy_config(config: dict, origin: tuple[str, str]) -> tuple[RequestPolicy, Path]:
    """Pure validation of the operator-supplied authorized policy contract."""
    data = config.get("server_policy")
    if not isinstance(data, dict) or data.get("verified") is not True:
        raise PolicyError("没有经过核实的服务器请求策略，禁止联网读取")
    if not isinstance(data.get("source"), str) or not data["source"].strip():
        raise PolicyError("服务器策略缺少可核查的出处")
    if data.get("origin") != "://".join(origin):
        raise PolicyError("服务器策略 origin 与当前请求域名不匹配")
    raw = data.get("windows")
    if not isinstance(raw, list) or not (1 <= len(raw) <= 8):
        raise PolicyError("必须配置经核实的滑动时间窗口")
    if any(not isinstance(item, (list, tuple)) or len(item) != 2 for item in raw):
        raise PolicyError("服务器配额必须为 [秒数, 最大请求数]")
    windows = tuple(WindowLimit(item[0], item[1]) for item in raw)
    db_raw = data.get("database")
    if not isinstance(db_raw, str) or not db_raw.strip() or len(db_raw) > 500:
        raise PolicyError("未配置固定持久化预算数据库路径")
    database = Path(db_raw)
    if db_raw == ":memory:" or not database.is_absolute():
        raise PolicyError("预算数据库必须为可共享的绝对本地路径")
    policy = RequestPolicy(windows, data.get("max_concurrency", 1), data["source"], True)
    return policy, database


def build_verified_budget(config: dict, origin: tuple[str, str]) -> GlobalRequestBudget:
    """Build per-origin budget; only after explicit independent policy review."""
    policy, database = read_verified_policy_config(config, origin)
    return GlobalRequestBudget(policy, database)
