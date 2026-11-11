"""Offline Windows monitor lifecycle regressions: no school or browser I/O."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from windows_gui import Application, MonitorEvent, MonitorWorker


class MonitorWorkerLifecycleTests(TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.worker = MonitorWorker({}, self.events)

    def events_collected(self):
        values = []
        while not self.events.empty():
            values.append(self.events.get_nowait())
        return values

    def test_natural_completion_is_distinct_from_manual_stop(self):
        with patch.object(MonitorWorker, "_run", new_callable=AsyncMock) as run:
            self.worker._thread_main()
        run.assert_awaited_once()
        events = self.events_collected()
        self.assertEqual([(event.kind, event.value) for event in events],
                         [("finished", "completed")])

    def test_stop_before_start_never_attempts_network(self):
        self.worker._stop_requested.set()
        # Real _run exits immediately when the stop flag is already set.
        with patch("windows_gui.KingoClient", side_effect=AssertionError("network")):
            self.worker._thread_main()
        events = self.events_collected()
        self.assertEqual([(event.kind, event.value) for event in events],
                         [("finished", "stopped")])

    def test_unexpected_error_is_redacted_and_not_reported_as_success(self):
        with patch.object(MonitorWorker, "_run", new_callable=AsyncMock,
                          side_effect=RuntimeError("secret school response")):
            self.worker._thread_main()
        events = self.events_collected()
        self.assertEqual(events[0].kind, "error")
        self.assertEqual(events[0].value, "RuntimeError")
        self.assertEqual((events[-1].kind, events[-1].value),
                         ("finished", "failed"))
        self.assertNotIn("secret", str(events))

    def test_handled_school_error_remains_failed(self):
        async def fake_run(worker):
            worker._had_scheduler_error = True

        with patch.object(MonitorWorker, "_run", fake_run):
            self.worker._thread_main()
        self.assertEqual(self.events_collected()[-1].value, "failed")


class MonitorGuiLifecycleTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            events=queue.Queue(),
            worker=object(),
            _closing=False,
            _tick_portal_appointment=Mock(),
            start_button=Mock(),
            edit_button=Mock(),
            plans_button=Mock(),
            config_entry=Mock(),
            pick_button=Mock(),
            open_button=Mock(),
            stop_button=Mock(),
            status=Mock(),
            log=Mock(),
            after=Mock(),
            destroy=Mock(),
        )

    def report(self, outcome, *events):
        for event in events:
            self.app.events.put(event)
        self.app.events.put(MonitorEvent("finished", outcome))
        Application.poll(self.app)

    def test_natural_end_restores_controls_and_final_status(self):
        self.report("completed")
        self.app.status.set.assert_called_with("已完成｜本次只读监测已结束")
        self.assertIsNone(self.app.worker)
        self.app.start_button.configure.assert_called_with(state="normal")
        self.app.stop_button.configure.assert_called_with(state="disabled")

    def test_manual_stop_does_not_remain_at_stopping(self):
        self.report("stopped")
        self.app.status.set.assert_called_with("已停止｜后台只读监测已结束")

    def test_failure_preserves_specific_stop_reason(self):
        reason = "模拟官方访问受限，任务已结束"
        self.report("failed", MonitorEvent("stop_reason", reason))
        self.app.status.set.assert_called_with("已停止｜" + reason)

    def test_uncaught_exception_remains_failure_after_finished(self):
        self.report("failed", MonitorEvent("error", "RuntimeError"))
        self.app.status.set.assert_called_with("监测异常终止")


if __name__ == "__main__":
    import unittest
    unittest.main()
