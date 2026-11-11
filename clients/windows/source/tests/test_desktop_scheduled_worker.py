"""Offline test coverage for the real Windows scheduled-execution thread.

All adapters in this module are synthetic and no school requests are made.
"""
import asyncio
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from core.course_models import CourseTarget
from core.enrollment import EnrollmentEngine, RunReport
from core.enrollment_schedule import ScheduledRunReport
from core.scheduled_ui_bridge import PreparedScheduledPlan
from desktop_scheduled_worker import DesktopScheduledWorker, ScheduledWorkerEvent


def plan():
    return PreparedScheduledPlan(
        "2026-12-21 09:00:00",
        (CourseTarget("A", "SYNTHETIC-C1", "SYNTHETIC-K1"),),
        (60, 60),
        60,
    )


def engine(*, verified=True, close=None):
    gateway = SimpleNamespace(
        official_catalog_verified=verified,
        official_enrollment_verified=verified,
        official_window_verified=verified,
        official_write_verified=verified,
        account_scope="synthetic-account",
    )
    if close is not None:
        gateway.aclose = close
    return EnrollmentEngine(
        gateway, SimpleNamespace(account_scope="synthetic-account"),
        user_opted_in=True,
        institution_permits_write=True,
    )


def completed(reason="window_timeout"):
    return ScheduledRunReport(
        reason,
        RunReport({"A": "本机测试结束，没有访问学校"}, True),
        1, 0.0,
    )


class DesktopScheduledWorkerTests(unittest.TestCase):
    def make(self, factory=None):
        events = []
        worker = DesktopScheduledWorker(
            plan(), factory or (lambda: engine()), events.append
        )
        return worker, events

    def finish(self, worker):
        worker.thread.join(timeout=4)
        self.assertFalse(worker.thread.is_alive())

    def test_completion_emits_waiting_result_and_finished(self):
        worker, events = self.make()
        with patch("desktop_scheduled_worker.run_prepared_schedule",
                   new_callable=AsyncMock, return_value=completed()) as run:
            worker.start()
            self.finish(worker)
        self.assertEqual([e.kind for e in events],
                         ["waiting", "result", "finished"])
        self.assertEqual(events[1].value.reason, "window_timeout")
        run.assert_awaited_once()
        self.assertIsNone(worker.loop)

    def test_unverified_adapter_blocks_before_scheduling(self):
        worker, events = self.make(lambda: engine(verified=False))
        with patch("desktop_scheduled_worker.run_prepared_schedule",
                   new_callable=AsyncMock) as run:
            worker.start()
            self.finish(worker)
        self.assertEqual([e.kind for e in events], ["blocked", "finished"])
        run.assert_not_awaited()

    def test_stop_before_start_does_not_create_adapter(self):
        factory = Mock()
        worker, events = self.make(factory)
        worker.stop()
        worker.start()
        self.finish(worker)
        factory.assert_not_called()
        self.assertEqual([e.kind for e in events], ["result", "finished"])
        self.assertEqual(events[0].value, "cancelled_before_start")

    def test_stop_during_local_wait_is_cooperative(self):
        waiting = threading.Event()
        events = []
        def record(event):
            events.append(event)
            if event.kind == "waiting":
                waiting.set()

        worker = DesktopScheduledWorker(plan(), lambda: engine(), record)

        async def simulated_wait(_plan, _engine, *, stop_event):
            await stop_event.wait()
            return completed("cancelled_waiting_for_window")

        with patch("desktop_scheduled_worker.run_prepared_schedule",
                   side_effect=simulated_wait) as run:
            worker.start()
            self.assertTrue(waiting.wait(timeout=4))
            worker.stop()
            self.finish(worker)
        self.assertEqual([e.kind for e in events],
                         ["waiting", "result", "finished"])
        self.assertEqual(events[1].value.reason, "cancelled_waiting_for_window")
        run.assert_awaited_once()

    def test_adapter_factory_failure_never_exposes_exception_text(self):
        worker, events = self.make(
            lambda: (_ for _ in ()).throw(RuntimeError("secret-token"))
        )
        worker.start()
        self.finish(worker)
        self.assertEqual([e.kind for e in events], ["error", "finished"])
        self.assertEqual(events[0].value, "RuntimeError")
        self.assertNotIn("secret", str(events))

    def test_cleanup_of_owned_gateway_on_completion(self):
        close = AsyncMock()
        worker, events = self.make(lambda: engine(close=close))
        with patch("desktop_scheduled_worker.run_prepared_schedule",
                   new_callable=AsyncMock, return_value=completed()):
            worker.start()
            self.finish(worker)
        close.assert_awaited_once()
        self.assertEqual(events[-1].kind, "finished")

    def test_cannot_launch_same_worker_twice(self):
        worker, _ = self.make()
        with patch("desktop_scheduled_worker.run_prepared_schedule",
                   new_callable=AsyncMock, return_value=completed()):
            worker.start()
            self.finish(worker)
        with self.assertRaises(RuntimeError):
            worker.start()


if __name__ == "__main__":
    unittest.main()
