"""Network-free V2 adaptive polling tests; no enrollment submissions."""
import asyncio
import unittest
from types import SimpleNamespace

from core.adaptive_read_cadence import AdaptiveReadCadence
from core.monitor_policy import validate_limits
from core.scheduler import Scheduler


def strategy(**adaptive):
    return {
        "mode": "monitor", "interval_range": [60, 60],
        "max_cycles": 2, "max_duration_minutes": 2,
        "adaptive_polling": {"enabled": True, **adaptive},
    }


class CadenceUnitTest(unittest.TestCase):
    def test_disabled_by_default_and_never_faster_than_baseline(self):
        cadence = AdaptiveReadCadence()
        cadence.observe_success({"request_ms": 90000, "budget_wait_ms": 1000})
        self.assertEqual(cadence.interval(60, floor=60), 60)
        self.assertEqual(cadence.report()["slowdown_multiplier"], 1)

    def test_latency_pressure_slows_then_recovers(self):
        cadence = AdaptiveReadCadence({"enabled": True})
        cadence.observe_success({"request_ms": 3000, "budget_wait_ms": 0})
        self.assertEqual(cadence.interval(60, floor=60), 120)
        cadence.observe_success({"request_ms": 80, "budget_wait_ms": 0})
        self.assertEqual(cadence.interval(60, floor=60), 90)
        for _ in range(12):
            cadence.observe_success({"request_ms": 80, "budget_wait_ms": 0})
        self.assertEqual(cadence.interval(60, floor=60), 60)
        self.assertEqual(cadence.report()["slow_samples"], 1)

    def test_budget_pressure_and_ceiling(self):
        cadence = AdaptiveReadCadence({"enabled": True, "max_multiplier": 16})
        for _ in range(8):
            cadence.observe_success({"request_ms": 100, "budget_wait_ms": 500})
        self.assertEqual(cadence.interval(300, floor=60), 3600)
        self.assertEqual(cadence.report()["slowdown_multiplier"], 16)

    def test_missing_bad_timing_does_not_fake_recovery(self):
        cadence = AdaptiveReadCadence({"enabled": True})
        cadence.observe_success({"request_ms": 3000, "budget_wait_ms": 0})
        for invalid in (None, {}, {"request_ms": float("nan"), "budget_wait_ms": 0},
                        {"request_ms": -1, "budget_wait_ms": 0},
                        {"request_ms": "fast", "budget_wait_ms": 0}):
            cadence.observe_success(invalid)
        self.assertEqual(cadence.interval(60, floor=60), 120)

    def test_invalid_inputs_fail_closed(self):
        for settings in (
            {"enabled": "yes"}, {"enabled": True, "slow_request_ms": -1},
            {"enabled": True, "budget_wait_ms": float("nan")},
            {"enabled": True, "max_multiplier": 99},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                AdaptiveReadCadence(settings)
        with self.assertRaises(ValueError):
            AdaptiveReadCadence({"enabled": True}).interval(30, floor=60)


class SchedulerIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_uses_real_success_timing_without_extra_queries(self):
        class FakeClient:
            def __init__(self):
                self.stop = asyncio.Event()
                self.last_timing = {}
                self.calls = 0

            async def fetch_course_list(self, *_):
                self.calls += 1
                self.last_timing = {"request_ms": 3000, "budget_wait_ms": 0}
                return []

        client = FakeClient()
        config = {
            "xnxq": "test-semester", "strategy": strategy(),
            "courses": [], "notify": {"enabled": False},
        }
        scheduler = Scheduler(client, None, config)
        delays = []

        async def fake_sleep(seconds):
            delays.append(seconds)
            return False  # user stopped after first scheduled read

        scheduler._sleep_or_stop = fake_sleep
        await scheduler.run()
        self.assertEqual(client.calls, 1)
        self.assertEqual(delays, [120.0])
        self.assertEqual(scheduler.cycles, 1)

    async def test_unchanged_without_opt_in(self):
        config = {
            "xnxq": "test-semester",
            "strategy": {**strategy(), "adaptive_polling": {"enabled": False}},
            "courses": [], "notify": {},
        }
        scheduler = Scheduler(SimpleNamespace(stop=asyncio.Event()), None, config)
        limits = validate_limits(config["strategy"])
        scheduler.adaptive.observe_success({"request_ms": 9000, "budget_wait_ms": 1000})
        self.assertEqual(scheduler._next_interval(limits), 60)


if __name__ == "__main__":
    unittest.main()
