"""Offline tests for the declared-override paths.

Two knobs can be lowered, and only with an explicit, recorded declaration:

* the polling floor (30 s by default) via ``absolute_floor_seconds``;
* the request-budget concurrency (1 by default) via
  ``parallel_lease_attested`` + ``parallel_source``.

Both defaults must stay exactly as they were, and both overrides must fail
closed when the declaration is missing or malformed.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from core.monitor_policy import (
    ABSOLUTE_MIN_INTERVAL, MIN_OVERRIDABLE_FLOOR, MonitorLimits, validate_limits,
)
from core.request_budget import (
    GlobalRequestBudget, PolicyError, RequestPolicy, WindowLimit,
    read_verified_policy_config,
)


def strategy(**policy_overrides) -> dict:
    policy = {"manually_confirmed": False}
    policy.update(policy_overrides)
    return {
        "interval_range": [60, 60],
        "max_cycles": 20,
        "max_duration_minutes": 480,
        "official_frequency_policy": policy,
    }


class FloorDefaultTests(unittest.TestCase):
    def test_default_floor_is_thirty_seconds(self):
        limits = validate_limits(strategy())
        self.assertEqual(limits.floor, ABSOLUTE_MIN_INTERVAL)
        self.assertFalse(limits.faster_than_default_floor)

    def test_below_floor_still_rejected_without_confirmation(self):
        payload = strategy()
        payload["interval_range"] = [10, 60]
        with self.assertRaises(ValueError):
            validate_limits(payload)

    def test_limits_post_init_enforces_floor(self):
        with self.assertRaises(ValueError):
            MonitorLimits(1.0, 60.0, 20, 480)
        with self.assertRaises(ValueError):
            MonitorLimits(60.0, 60.0, 20, 480, floor=1.0)


class FloorOverrideTests(unittest.TestCase):
    def confirmed(self, **extra) -> dict:
        policy = {
            "manually_confirmed": True,
            "source": "教务处 2026-11 公告：允许 10 秒间隔",
            "min_interval_seconds": 10,
        }
        policy.update(extra)
        payload = strategy()
        payload["interval_range"] = [10, 60]
        payload["official_frequency_policy"] = policy
        return payload

    def test_attested_override_lowers_the_floor(self):
        limits = validate_limits(self.confirmed(
            absolute_floor_seconds=10, absolute_floor_verified=True))
        self.assertEqual(limits.floor, 10.0)
        self.assertTrue(limits.faster_than_default_floor)
        self.assertEqual(limits.low, 10.0)

    def test_override_without_verified_flag_rejected(self):
        with self.assertRaises(ValueError):
            validate_limits(self.confirmed(
                absolute_floor_seconds=10, absolute_floor_verified=False))

    def test_override_without_source_rejected(self):
        payload = self.confirmed(
            absolute_floor_seconds=10, absolute_floor_verified=True)
        payload["official_frequency_policy"]["source"] = ""
        payload["official_frequency_policy"]["absolute_floor_source"] = ""
        with self.assertRaises(ValueError):
            validate_limits(payload)

    def test_override_below_hard_minimum_rejected(self):
        with self.assertRaises(ValueError):
            validate_limits(self.confirmed(
                absolute_floor_seconds=MIN_OVERRIDABLE_FLOOR - 0.5,
                absolute_floor_verified=True))

    def test_override_above_default_rejected(self):
        with self.assertRaises(ValueError):
            validate_limits(self.confirmed(
                absolute_floor_seconds=31, absolute_floor_verified=True))

    def test_override_without_confirmation_rejected(self):
        payload = strategy(absolute_floor_seconds=10, absolute_floor_verified=True,
                           source="x")
        payload["interval_range"] = [10, 60]
        with self.assertRaises(ValueError):
            validate_limits(payload)

    def test_override_non_numeric_rejected(self):
        for value in ("10", None, True, float("inf"), float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_limits(self.confirmed(
                        absolute_floor_seconds=value,
                        absolute_floor_verified=True))

    def test_interval_below_the_new_floor_still_rejected(self):
        payload = self.confirmed(absolute_floor_seconds=10,
                                 absolute_floor_verified=True)
        payload["interval_range"] = [1, 60]
        with self.assertRaises(ValueError):
            validate_limits(payload)


class ParallelPolicyTests(unittest.TestCase):
    def windows(self):
        return (WindowLimit(60.0, 2),)

    def test_default_concurrency_is_one(self):
        policy = RequestPolicy(self.windows(), 1, "src", True, "https://x")
        self.assertEqual(policy.max_concurrency, 1)
        self.assertTrue(policy.serial_lease_required)

    def test_two_without_attestation_rejected(self):
        with self.assertRaises(PolicyError):
            RequestPolicy(self.windows(), 2, "src", True, "https://x")

    def test_two_with_attestation_and_source_accepted(self):
        policy = RequestPolicy(self.windows(), 2, "src", True, "https://x",
                               parallel_lease_attested=True,
                               parallel_source="多账号各自独立预算库")
        self.assertFalse(policy.serial_lease_required)

    def test_two_with_attestation_but_no_source_rejected(self):
        with self.assertRaises(PolicyError):
            RequestPolicy(self.windows(), 2, "src", True, "https://x",
                          parallel_lease_attested=True, parallel_source=" ")

    def test_serial_with_attestation_rejected(self):
        with self.assertRaises(PolicyError):
            RequestPolicy(self.windows(), 1, "src", True, "https://x",
                          parallel_lease_attested=True, parallel_source="x")

    def test_range_is_one_to_sixteen(self):
        for value in (0, 17, -1, "2", 2.0, True):
            with self.subTest(value=value):
                with self.assertRaises(PolicyError):
                    RequestPolicy(self.windows(), value, "src", True,
                                  "https://x", parallel_lease_attested=True,
                                  parallel_source="x")

    def test_config_passes_parallel_fields(self):
        config = {
            "server_policy": {
                "verified": True, "source": "policy", "origin": "https://x",
                "windows": [[60, 4]], "max_concurrency": 4,
                "database": "D:/kingo/budget/p.sqlite",
                "parallel_lease_attested": True,
                "parallel_source": "四个账号四个预算库",
            }
        }
        policy, database = read_verified_policy_config(config, ("https", "x"))
        self.assertEqual(policy.max_concurrency, 4)
        self.assertFalse(policy.serial_lease_required)
        self.assertEqual(str(database), str(Path("D:/kingo/budget/p.sqlite")))

    def test_config_without_declaration_keeps_serial(self):
        config = {
            "server_policy": {
                "verified": True, "source": "policy", "origin": "https://x",
                "windows": [[60, 4]], "max_concurrency": 4,
                "database": "D:/kingo/budget/p.sqlite",
            }
        }
        with self.assertRaises(PolicyError):
            read_verified_policy_config(config, ("https", "x"))


class ParallelRuntimeTests(unittest.TestCase):
    def test_parallel_policy_skips_the_serial_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = RequestPolicy(
                (WindowLimit(60.0, 10),), 3, "src", True, "https://x",
                parallel_lease_attested=True, parallel_source="多账号独立库")
            budget = GlobalRequestBudget(policy, Path(tmp) / "p.sqlite")

            async def scenario():
                order = []

                async def one(tag):
                    async with budget.request():
                        order.append(tag)
                        await asyncio.sleep(0.01)

                await asyncio.gather(*(one(i) for i in range(3)))
                return order

            order = asyncio.run(scenario())
            self.assertEqual(sorted(order), [0, 1, 2])
            self.assertFalse(budget.lock_path.exists()
                             and budget.lock_path.stat().st_size > 1)

    def test_serial_policy_still_holds_the_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = RequestPolicy((WindowLimit(60.0, 10),), 1, "src", True,
                                   "https://x")
            budget = GlobalRequestBudget(policy, Path(tmp) / "p.sqlite")
            self.assertTrue(policy.serial_lease_required)
            self.assertIn("request.lock", budget.lock_path.name)

    def test_signature_includes_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.sqlite"
            first = RequestPolicy((WindowLimit(60.0, 10),), 1, "src", True,
                                  "https://x")
            GlobalRequestBudget(first, path)
            second = RequestPolicy((WindowLimit(60.0, 10),), 2, "src", True,
                                   "https://x", parallel_lease_attested=True,
                                   parallel_source="x")
            with self.assertRaises(PolicyError):
                GlobalRequestBudget(second, path)


if __name__ == "__main__":
    unittest.main()
