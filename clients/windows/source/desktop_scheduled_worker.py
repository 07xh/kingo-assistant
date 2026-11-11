"""Thread-owned scheduled enrollment executor for the Windows desktop.

No NCWU adapter is registered by this module. The caller must explicitly
inject a real, capability-gated EnrollmentEngine. Neither a local plan nor
a successful offline preflight enables school HTTP traffic.

The Tk owner thread only receives immutable events via the provided callback.
A stop request cancels pre-start/window waiting; it deliberately does NOT
interrupt an in-flight submit/official-result readback.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
from typing import Callable

from core.enrollment import EnrollmentEngine, EnrollmentNotReady
from core.scheduled_ui_bridge import (
    PreparedScheduledPlan, run_prepared_schedule,
)
from core.enrollment_schedule import ScheduledRunReport


@dataclass(frozen=True, slots=True)
class ScheduledWorkerEvent:
    kind: str
    value: object = None


class DesktopScheduledWorker:
    def __init__(
        self,
        plan: PreparedScheduledPlan,
        engine_factory: Callable[[], EnrollmentEngine],
        send: Callable[[ScheduledWorkerEvent], None],
    ) -> None:
        if not isinstance(plan, PreparedScheduledPlan):
            raise TypeError("预定计划类型错误")
        if not callable(engine_factory) or not callable(send):
            raise TypeError("需要明确的执行器构造器与事件回调")
        self.plan = plan
        self.engine_factory = engine_factory
        self.send = send
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.cancel: asyncio.Event | None = None
        self._stop_requested = threading.Event()
        self._state_lock = threading.Lock()

    def start(self) -> None:
        with self._state_lock:
            if self.thread is not None:
                raise RuntimeError("本次预约任务不可重复启动")
            self.thread = threading.Thread(
                target=self._thread_main,
                name="KingoScheduledEnrollment",
                daemon=True,
            )
            self.thread.start()

    def stop(self) -> None:
        """Request cancellation without aborting a potentially sent write."""
        self._stop_requested.set()
        with self._state_lock:
            loop, cancel = self.loop, self.cancel
        if loop is not None and cancel is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(cancel.set)
            except RuntimeError:
                # The worker already finished and closed its event loop.
                pass

    async def _execute(self) -> None:
        loop = asyncio.get_running_loop()
        cancel = asyncio.Event()
        with self._state_lock:
            self.loop, self.cancel = loop, cancel
            if self._stop_requested.is_set():
                cancel.set()
        if cancel.is_set():
            self.send(ScheduledWorkerEvent("result", "cancelled_before_start"))
            return

        # A missing/unverified factory must fail before the local wait,
        # never silently switch to a browser or fictional gateway.
        engine = self.engine_factory()
        if not isinstance(engine, EnrollmentEngine):
            raise EnrollmentNotReady("当前没有已验收的自动选课执行器")
        report: ScheduledRunReport | None = None
        try:
            engine.validate_ready()
            self.send(ScheduledWorkerEvent("waiting"))
            report = await run_prepared_schedule(
                self.plan, engine, stop_event=cancel
            )
        finally:
            # Always close an owned gateway, including after a rejected gate.
            # Cleanup failures are reported as non-fatal diagnostics, not as
            # a substitute for the independently verified enrollment result.
            closer = getattr(engine.gateway, "aclose", None)
            if callable(closer):
                try:
                    await closer()
                except Exception as exc:
                    self.send(ScheduledWorkerEvent(
                        "cleanup_warning", type(exc).__name__
                    ))
        if report is not None:
            self.send(ScheduledWorkerEvent("result", report))

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._execute())
        except EnrollmentNotReady:
            self.send(ScheduledWorkerEvent("blocked"))
        except Exception as exc:
            # Never echo authentication, protocol response bodies or URLs.
            self.send(ScheduledWorkerEvent("error", type(exc).__name__))
        finally:
            with self._state_lock:
                self.loop, self.cancel = None, None
            self.send(ScheduledWorkerEvent("finished"))
