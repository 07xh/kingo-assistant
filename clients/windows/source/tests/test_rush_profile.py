"""Offline tests for the write-phase burst profile.

The read poller's 30 s floor is not under test here; what is under test is that
the write path has its own, faster, still-bounded cadence, that every ceiling
fails closed, and that an unknown config key cannot look like a working knob.
"""
import unittest

from core.rush_profile import (
    MAX_CONCURRENCY, MIN_INTERVAL_FLOOR, PRESETS, RushError, RushProfile,
    baseline_requests_per_minute, from_mapping, speedup,
)


class DefaultProfileTests(unittest.TestCase):
    def test_defaults_are_the_standard_preset(self):
        profile = RushProfile()
        self.assertEqual(profile.concurrency, PRESETS["standard"]["concurrency"])
        self.assertEqual(profile.min_interval, PRESETS["standard"]["min_interval"])
        self.assertGreater(profile.concurrency, 1)

    def test_effective_interval_takes_the_window_average_when_larger(self):
        profile = RushProfile(concurrency=8, min_interval=0.25,
                              window_seconds=60.0, window_requests=150)
        self.assertAlmostEqual(profile.effective_interval, 0.4)
        self.assertAlmostEqual(profile.requests_per_minute, 150.0)

    def test_interval_wins_when_it_is_the_tighter_bound(self):
        profile = RushProfile(concurrency=4, min_interval=1.0,
                              window_seconds=60.0, window_requests=600)
        self.assertAlmostEqual(profile.effective_interval, 1.0)
        self.assertAlmostEqual(profile.requests_per_minute, 60.0)

    def test_burst_is_much_faster_than_the_serial_baseline(self):
        profile = RushProfile()
        baseline = baseline_requests_per_minute(30.0)
        self.assertAlmostEqual(baseline, 2.0)
        self.assertGreater(speedup(profile, accounts=1), 50.0)
        self.assertGreater(speedup(profile, accounts=4), 200.0)

    def test_describe_mentions_the_real_numbers(self):
        text = RushProfile(concurrency=12, min_interval=0.1,
                           window_seconds=60.0, window_requests=300).describe()
        self.assertIn("并发 12", text)
        self.assertIn("0.2 s", text)


class CeilingTests(unittest.TestCase):
    def test_concurrency_above_ceiling_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(concurrency=MAX_CONCURRENCY + 1)

    def test_concurrency_below_one_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(concurrency=0)

    def test_interval_below_floor_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(min_interval=MIN_INTERVAL_FLOOR / 2)

    def test_interval_above_ceiling_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(min_interval=60.0)

    def test_backoff_cap_below_base_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(backoff_base=1.0, backoff_cap=0.5)

    def test_boolean_is_not_an_integer(self):
        with self.assertRaises(RushError):
            RushProfile(concurrency=True)

    def test_zero_attempts_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(max_attempts=0)

    def test_zero_breaker_is_refused(self):
        with self.assertRaises(RushError):
            RushProfile(circuit_breaker=0)


class MappingTests(unittest.TestCase):
    def test_preset_selects_and_overrides_apply(self):
        profile = from_mapping({"preset": "extreme", "concurrency": 4,
                                "stagger_ms": 0})
        self.assertEqual(profile.preset, "extreme")
        self.assertEqual(profile.concurrency, 4)
        self.assertEqual(profile.stagger_ms, 0)
        self.assertEqual(profile.min_interval, PRESETS["extreme"]["min_interval"])

    def test_unknown_key_is_refused(self):
        with self.assertRaises(RushError):
            from_mapping({"preset": "standard", "min_interval_ms": 100})

    def test_unknown_preset_is_refused(self):
        with self.assertRaises(RushError):
            from_mapping({"preset": "insane"})

    def test_single_window_shorthand(self):
        profile = from_mapping({"preset": "safe", "windows": [[30, 60]]})
        self.assertEqual(profile.window_seconds, 30.0)
        self.assertEqual(profile.window_requests, 60)

    def test_window_shorthand_rejects_two_windows(self):
        with self.assertRaises(RushError):
            from_mapping({"preset": "safe", "windows": [[30, 60], [60, 120]]})

    def test_non_mapping_is_refused(self):
        with self.assertRaises(RushError):
            from_mapping(["standard"])

    def test_with_overrides_ignores_none(self):
        profile = RushProfile()
        self.assertEqual(profile.with_overrides(concurrency=None), profile)

    def test_every_preset_builds(self):
        for name in PRESETS:
            profile = from_mapping({"preset": name})
            self.assertEqual(profile.preset, name)
            self.assertGreaterEqual(profile.requests_per_minute, 30.0)


class BaselineTests(unittest.TestCase):
    def test_zero_baseline_interval_is_refused(self):
        with self.assertRaises(RushError):
            baseline_requests_per_minute(0)

    def test_speedup_needs_positive_account_count(self):
        with self.assertRaises(RushError):
            speedup(RushProfile(), accounts=0)


if __name__ == "__main__":
    unittest.main()
