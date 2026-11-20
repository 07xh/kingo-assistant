"""Offline tests for the opt-in burst read cadence.

The point of these tests is the *boundary*: the 30 s monitor floor must stay the
default, and going faster must require an explicit ``drive_read_cadence: true``
that a typo or a stray number cannot accidentally switch on.
"""
import random
import unittest

from core.rush_profile import RushError
from core.rush_read import RushReadCadence, build_rush_read, rush_of


class DefaultTests(unittest.TestCase):
    def test_absent_block_gives_no_cadence(self):
        self.assertIsNone(build_rush_read({}))

    def test_present_block_without_the_switch_gives_no_cadence(self):
        self.assertIsNone(build_rush_read({"rush": {"preset": "standard"}}))

    def test_switch_off_is_explicitly_off(self):
        self.assertIsNone(build_rush_read(
            {"rush": {"preset": "extreme", "drive_read_cadence": False}}))

    def test_none_config_is_safe(self):
        self.assertIsNone(build_rush_read(None))
        self.assertIsNone(rush_of(None))

    def test_strategy_nested_block_is_also_read(self):
        self.assertIsNotNone(build_rush_read(
            {"strategy": {"rush": {"preset": "safe", "drive_read_cadence": True}}}))

    def test_non_mapping_block_is_ignored(self):
        self.assertIsNone(build_rush_read({"rush": "standard"}))


class EnabledTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cadence = build_rush_read(
            {"rush": {"preset": "standard", "drive_read_cadence": True}})

    def test_interval_is_the_burst_interval_not_thirty_seconds(self):
        self.assertIsNotNone(self.cadence)
        self.assertLess(self.cadence.interval_seconds, 1.0)
        self.assertGreater(self.cadence.interval_seconds, 0.0)

    def test_jitter_stays_within_the_band(self):
        rng = random.Random(7)
        band = self.cadence.interval_seconds * self.cadence.jitter
        for _ in range(200):
            value = self.cadence.next_interval(rng)
            self.assertGreaterEqual(value, 0.05)
            self.assertLessEqual(value, self.cadence.interval_seconds + band)

    def test_as_dict_is_json_safe(self):
        payload = self.cadence.as_dict()
        self.assertIn("interval_seconds", payload)
        self.assertIn("rush[standard]", payload["source"])

    def test_interval_is_capped_at_a_minute(self):
        cadence = build_rush_read({"rush": {"preset": "safe", "min_interval": 5.0,
                                            "drive_read_cadence": True}})
        self.assertLessEqual(cadence.interval_seconds, 60.0)

    def test_bad_profile_fails_loudly(self):
        with self.assertRaises(RushError):
            build_rush_read({"rush": {"preset": "standard", "concurrency": 99,
                                      "drive_read_cadence": True}})

    def test_string_boolean_is_refused(self):
        with self.assertRaises(RushError):
            build_rush_read({"rush": {"drive_read_cadence": "false"}})


class CadenceValueTests(unittest.TestCase):
    def test_zero_interval_is_refused(self):
        with self.assertRaises(ValueError):
            RushReadCadence(interval_seconds=0.0)

    def test_negative_jitter_is_refused(self):
        with self.assertRaises(ValueError):
            RushReadCadence(interval_seconds=1.0, jitter=-0.1)

    def test_jitter_never_produces_zero(self):
        cadence = RushReadCadence(interval_seconds=0.06, jitter=1.0)
        rng = random.Random(3)
        for _ in range(100):
            self.assertGreater(cadence.next_interval(rng), 0.0)


class SchedulerWiringTests(unittest.TestCase):
    def _scheduler(self, config):
        from core.scheduler import Scheduler
        scheduler = Scheduler.__new__(Scheduler)
        scheduler.pickup = None
        scheduler.rush = build_rush_read(config)
        from core.adaptive_read_cadence import AdaptiveReadCadence
        scheduler.adaptive = AdaptiveReadCadence(None)
        return scheduler

    def _limits(self):
        from core.monitor_policy import MonitorLimits
        return MonitorLimits(30.0, 60.0, 10, 60)

    def test_monitor_range_is_used_when_rush_is_absent(self):
        scheduler = self._scheduler({})
        interval = scheduler._next_interval(self._limits())
        self.assertGreaterEqual(interval, 30.0)
        self.assertLessEqual(interval, 60.0)

    def test_burst_interval_replaces_the_monitor_floor_when_enabled(self):
        scheduler = self._scheduler(
            {"rush": {"preset": "standard", "drive_read_cadence": True}})
        interval = scheduler._next_interval(self._limits())
        self.assertLess(interval, 1.0)
        self.assertGreater(interval, 0.0)


if __name__ == "__main__":
    unittest.main()
