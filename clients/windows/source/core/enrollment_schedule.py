"""Scheduled, low-frequency wait-for-window runner.

This coordinates local start time with an explicitly verified official
selection-window probe. It never infers that a window is open from the clock.
The underlying EnrollmentEngine re-checks the window before any write.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import random
import time
from typing import Callable, Protocol, Sequence

from core.enrollment import (
    EnrollmentEngine, RunReport, Target, WindowState, _validate_targets,
)


MIN_WINDOW_POLL_SECONDS = 60
MAX_WINDOW_POLL_SECONDS = 3600
MIN_WINDOW_WAIT_MINUTES = 1
MAX_WINDOW_WAIT_MINUTES = 1440


class ScheduleClock(Protocol):
    def parse(self, value: str) -> float: ...
    def now(self) -> float: ...


@dataclass(frozen=True)
class ScheduledRunReport:
    reason: str
    result: RunReport
    window_checks: int
    waited_seconds: float


class ScheduledEnrollmentRunner:
    """Wait for the configured local time, then probe the official window.

    The window is checked at a bounded interval (60–3600 seconds). Only an
    explicit WindowState.OPEN starts EnrollmentEngine. CLOSED continues waiting;
    UNKNOWN, exceptions, timeout, or cancellation stop without a write.
    """

    def __init__(
        self,
        engine: EnrollmentEngine,
        clock: ScheduleClock,
        *,
        poll_interval_range: tuple[int, int] = (60, 60),
        max_wait_minutes: int = 480,
        sleep: Callable[[float], object] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        uniform: Callable[[float, float], float] = random.uniform,
    ):
        if (not isinstance(poll_interval_range, (tuple, list))
                or len(poll_interval_range) != 2
                or any(type(x) is not int for x in poll_interval_range)):
            raise ValueError("选课窗口轮询间隔必须是两个整数秒")
        low, high = poll_interval_range
        if not MIN_WINDOW_POLL_SECONDS <= low <= high <= MAX_WINDOW_POLL_SECONDS:
            raise ValueError("选课窗口轮询间隔必须在 60–3600 秒范围内")
        if (type(max_wait_minutes) is not int
                or not MIN_WINDOW_WAIT_MINUTES <= max_wait_minutes <= MAX_WINDOW_WAIT_MINUTES):
            raise ValueError("等待选课窗口时长必须为 1–1440 分钟")
        self.engine = engine
        self.clock = clock
        self.low = low
        self.high = high
        self.max_wait_minutes = max_wait_minutes
        self.sleep = sleep
        self.monotonic = monotonic
        self.uniform = uniform

    @staticmethod
    def _result(targets: Sequence[Target], message: str) -> RunReport:
        return RunReport({target.key: message for target in targets}, True)

    async def _pause(self, seconds: float, stop_event: asyncio.Event | None) -> bool:
        if stop_event is None:
            await self.sleep(seconds)
            return False
        if stop_event.is_set():
            return True
        sleep_task = asyncio.create_task(self.sleep(seconds))
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            (sleep_task, stop_task), return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if stop_task in done and stop_event.is_set():
            return True
        if sleep_task in done:
            await sleep_task
        return False

    async def run(
        self,
        targets: Sequence[Target],
        target_time: str,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> ScheduledRunReport:
        ordered = list(targets)
        _validate_targets(ordered)
        # Check permissions and all adapter capabilities before waiting.
        self.engine.validate_ready()
        try:
            target_epoch = float(self.clock.parse(target_time))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("自动选课计划时间格式无效") from exc
        if not math.isfinite(target_epoch):
            raise ValueError("自动选课计划时间必须是有限时间")

        started = self.monotonic()
        while self.clock.now() < target_epoch:
            if stop_event is not None and stop_event.is_set():
                return ScheduledRunReport(
                    "cancelled_before_start",
                    self._result(ordered, "已取消，未发起学校请求"),
                    0, max(0.0, self.monotonic() - started),
                )
            remaining = target_epoch - self.clock.now()
            # Avoid waking the local event loop twice per second for days.
            # Coarse waits are cancellation-aware; use subsecond precision
            # only in the final 10 seconds before the confirmed start time.
            step = 30.0 if remaining > 60 else (5.0 if remaining > 10 else 0.25)
            if await self._pause(min(step, remaining), stop_event):
                return ScheduledRunReport(
                    "cancelled_before_start",
                    self._result(ordered, "已取消，未发起学校请求"),
                    0, max(0.0, self.monotonic() - started),
                )

        deadline = self.monotonic() + self.max_wait_minutes * 60
        checks = 0
        while self.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return ScheduledRunReport(
                    "cancelled_waiting_for_window",
                    self._result(ordered, "已取消，未提交选课"),
                    checks, max(0.0, self.monotonic() - started),
                )
            try:
                state = await self.engine.gateway.selection_window()
            except Exception:
                state = WindowState.UNKNOWN
            checks += 1
            if not isinstance(state, WindowState):
                state = WindowState.UNKNOWN
            if state is WindowState.OPEN:
                result = await self.engine.run(ordered)
                reason = "engine_halted" if result.halted else "engine_completed"
                return ScheduledRunReport(
                    reason, result, checks, max(0.0, self.monotonic() - started)
                )
            if state is WindowState.UNKNOWN:
                return ScheduledRunReport(
                    "window_unknown",
                    self._result(ordered, "选课窗口状态无法确认，已停止"),
                    checks, max(0.0, self.monotonic() - started),
                )

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                break
            interval = self.uniform(self.low, self.high)
            if not math.isfinite(interval) or interval < self.low or interval > self.high:
                return ScheduledRunReport(
                    "invalid_poll_interval",
                    self._result(ordered, "本地轮询间隔生成异常，已停止"),
                    checks, max(0.0, self.monotonic() - started),
                )
            if await self._pause(min(interval, remaining), stop_event):
                return ScheduledRunReport(
                    "cancelled_waiting_for_window",
                    self._result(ordered, "已取消，未提交选课"),
                    checks, max(0.0, self.monotonic() - started),
                )

        return ScheduledRunReport(
            "window_timeout",
            self._result(ordered, "等待选课窗口开放超时，未提交选课"),
            checks, max(0.0, self.monotonic() - started),
        )
