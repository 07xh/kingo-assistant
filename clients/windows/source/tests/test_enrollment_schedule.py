"""Offline tests for the scheduled official-window coordinator."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady, Offering,
    SubmitReceipt, Target, WindowState,
)
from core.enrollment_schedule import ScheduledEnrollmentRunner
from core.course_models import ScheduleSnapshot


class FakeClock:
    def __init__(self, current=1000.0):
        self.current = current

    def parse(self, value):
        return float(value)

    def now(self):
        return self.current


class FakeGateway:
    official_catalog_verified = True
    official_enrollment_verified = True
    official_window_verified = True
    official_write_verified = True
    account_scope = "schedule-account"
    enrollment_semester = "2026-2027-1"

    def __init__(self, windows):
        self.windows = list(windows)
        self.window_calls = 0
        self.catalog_calls = 0
        self.enrolled = set()
        self.writes = []

    async def selection_window(self):
        index = min(self.window_calls, len(self.windows) - 1)
        self.window_calls += 1
        return self.windows[index]

    async def read_enrollments(self):
        return set(self.enrolled)

    async def list_offerings(self):
        self.catalog_calls += 1
        return [Offering("C1", "K1", 1, semester=self.enrollment_semester,
                         time_slots=())]

    async def read_schedule(self):
        return ScheduleSnapshot(self.enrollment_semester, (), True)

    async def submit_once(self, target):
        self.writes.append(target)
        self.enrolled.add((target.course_id, target.class_id))
        return SubmitReceipt(True)


class ScheduledEnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Target("A", "C1", "K1")
        self.clock = FakeClock()
        self.monotonic_value = [0.0]
        self.gateway = FakeGateway([WindowState.OPEN, WindowState.OPEN])
        self.journal = AttemptJournal(
            Path(self.temp.name) / "attempts.json",
            "2026-2027-1",
            account_scope="schedule-account",
        )
        self.engine = EnrollmentEngine(
            self.gateway, self.journal,
            user_opted_in=True, institution_permits_write=True,
        )

    def make_runner(self, *, gateway=None, poll=(60, 60), wait=2, stop_event=None):
        if gateway is not None:
            self.gateway = gateway
            self.engine = EnrollmentEngine(
                gateway, self.journal,
                user_opted_in=True, institution_permits_write=True,
            )

        async def fake_sleep(seconds):
            self.clock.current += seconds
            self.monotonic_value[0] += seconds
            if stop_event is not None and stop_event.get("during_sleep"):
                stop_event["event"].set()

        return ScheduledEnrollmentRunner(
            self.engine, self.clock,
            poll_interval_range=poll,
            max_wait_minutes=wait,
            sleep=fake_sleep,
            monotonic=lambda: self.monotonic_value[0],
            uniform=lambda low, high: low,
        )

    def run_runner(self, runner, target_time="1000", stop_event=None):
        event = stop_event["event"] if stop_event is not None else None
        return asyncio.run(runner.run([self.target], target_time, stop_event=event))

    def test_waits_until_scheduled_time_before_first_window_probe(self):
        runner = self.make_runner()
        report = self.run_runner(runner, target_time="1001")
        self.assertEqual(report.reason, "engine_completed")
        self.assertGreaterEqual(self.clock.current, 1001)
        self.assertEqual(report.window_checks, 1)
        self.assertEqual(len(self.gateway.writes), 1)

    def test_closed_window_rechecks_at_bounded_interval_then_starts(self):
        gateway = FakeGateway([WindowState.CLOSED, WindowState.OPEN, WindowState.OPEN])
        runner = self.make_runner(gateway=gateway, poll=(60, 60))
        report = self.run_runner(runner)
        self.assertEqual(report.reason, "engine_completed")
        self.assertEqual(report.window_checks, 2)
        self.assertEqual(self.clock.current, 1060)
        self.assertEqual(gateway.catalog_calls, 1)
        self.assertEqual(len(gateway.writes), 1)

    def test_closed_window_timeout_never_reads_catalog_or_writes(self):
        gateway = FakeGateway([WindowState.CLOSED])
        runner = self.make_runner(gateway=gateway, poll=(60, 60), wait=1)
        report = self.run_runner(runner)
        self.assertEqual(report.reason, "window_timeout")
        self.assertEqual(report.window_checks, 1)
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(gateway.writes, [])

    def test_unknown_window_stops_without_retries(self):
        gateway = FakeGateway([WindowState.UNKNOWN])
        runner = self.make_runner(gateway=gateway)
        report = self.run_runner(runner)
        self.assertEqual(report.reason, "window_unknown")
        self.assertEqual(report.window_checks, 1)
        self.assertEqual(gateway.window_calls, 1)
        self.assertEqual(gateway.writes, [])

    def test_non_enum_window_state_is_treated_as_unknown(self):
        gateway = FakeGateway(["open"])
        runner = self.make_runner(gateway=gateway)
        report = self.run_runner(runner)
        self.assertEqual(report.reason, "window_unknown")
        self.assertEqual(gateway.writes, [])

    def test_window_closing_after_scheduler_probe_is_caught_by_engine(self):
        gateway = FakeGateway([WindowState.OPEN, WindowState.CLOSED])
        runner = self.make_runner(gateway=gateway)
        report = self.run_runner(runner)
        self.assertEqual(report.reason, "engine_halted")
        self.assertTrue(report.result.halted)
        self.assertEqual(report.result.states["A"], "学校选课窗口未开放")
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(gateway.writes, [])

    def test_cancelled_before_start_does_not_probe_window(self):
        event = asyncio.Event()
        event.set()
        state = {"event": event}
        runner = self.make_runner(stop_event=state)
        report = self.run_runner(runner, target_time="9999", stop_event=state)
        self.assertEqual(report.reason, "cancelled_before_start")
        self.assertEqual(self.gateway.window_calls, 0)
        self.assertEqual(self.gateway.writes, [])

    def test_cancel_during_closed_window_wait_stops_without_write(self):
        event = asyncio.Event()
        state = {"event": event, "during_sleep": True}
        gateway = FakeGateway([WindowState.CLOSED])
        runner = self.make_runner(gateway=gateway, stop_event=state)
        report = self.run_runner(runner, stop_event=state)
        self.assertEqual(report.reason, "cancelled_waiting_for_window")
        self.assertEqual(report.window_checks, 1)
        self.assertEqual(gateway.writes, [])

    def test_capability_gate_is_checked_before_wait_or_probe(self):
        self.gateway.official_write_verified = False
        runner = self.make_runner()
        with self.assertRaises(EnrollmentNotReady):
            self.run_runner(runner, target_time="9999")
        self.assertEqual(self.gateway.window_calls, 0)
        self.assertEqual(self.monotonic_value[0], 0)

    def test_long_local_wait_does_not_wake_every_half_second(self):
        pauses = []

        async def fake_sleep(seconds):
            pauses.append(seconds)
            self.clock.current += seconds
            self.monotonic_value[0] += seconds

        runner = ScheduledEnrollmentRunner(
            self.engine, self.clock,
            poll_interval_range=(60, 60),
            max_wait_minutes=2,
            sleep=fake_sleep,
            monotonic=lambda: self.monotonic_value[0],
            uniform=lambda low, high: low,
        )
        report = self.run_runner(runner, target_time="4600")
        self.assertEqual(report.reason, "engine_completed")
        self.assertEqual(report.window_checks, 1)
        self.assertEqual(len(self.gateway.writes), 1)
        self.assertIn(30.0, pauses)
        self.assertIn(5.0, pauses)
        self.assertIn(0.25, pauses)
        self.assertLess(len(pauses), 200)  # instead of 7200 timer wakeups

    def test_poll_interval_and_timeout_bounds(self):
        for interval, wait in [((0, 1), 2), ((60, 3601), 2), ((90, 60), 2),
                               ((60, 60), 0), ((60, 60), 1441)]:
            with self.assertRaises(ValueError):
                ScheduledEnrollmentRunner(
                    self.engine, self.clock,
                    poll_interval_range=interval,
                    max_wait_minutes=wait,
                )

    def test_invalid_schedule_time_is_rejected_before_network(self):
        runner = self.make_runner()
        with self.assertRaises(ValueError):
            self.run_runner(runner, target_time="not-a-time")
        self.assertEqual(self.gateway.window_calls, 0)


if __name__ == "__main__":
    unittest.main()
