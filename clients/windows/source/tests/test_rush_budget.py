"""Offline tests for burst pacing: interval, window cap, in-flight cap, cooldown.

Time is faked where determinism matters (a pacer's arithmetic must be exact to
the millisecond, not to the scheduler's jitter) and real where concurrency is
the thing under test.
"""
import asyncio
import unittest

from core.rush_budget import AccountPacer, BudgetError, RushBudget
from core.rush_profile import RushProfile


class FakeClock:
    """Monotonic clock whose only source of advance is the injected sleeper."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(0.0, float(seconds))
        await asyncio.sleep(0)


def profile(**overrides) -> RushProfile:
    base = {
        "preset": "test",
        "concurrency": 4,
        "min_interval": 0.1,
        "window_seconds": 60.0,
        "window_requests": 600,
        "stagger_ms": 0,
        "backoff_base": 0.01,
        "backoff_cap": 0.02,
        "deadline_seconds": 30.0,
    }
    base.update(overrides)
    return RushProfile(**base)


class PacerIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_send_is_immediate_and_the_next_waits_one_interval(self):
        clock = FakeClock()
        pacer = AccountPacer("a", profile(min_interval=0.5), clock=clock,
                             sleeper=clock.sleep)
        self.assertEqual(await pacer.acquire(), 0.0)
        slept = await pacer.acquire()
        self.assertGreaterEqual(slept, 0.5)
        self.assertEqual(pacer.stats.sent, 2)
        self.assertEqual(pacer.stats.waits, 1)

    async def test_window_cap_blocks_even_when_the_interval_would_allow(self):
        clock = FakeClock()
        pacer = AccountPacer("a", profile(min_interval=0.05, window_seconds=1.0,
                                          window_requests=2),
                             clock=clock, sleeper=clock.sleep)
        await pacer.acquire()
        await pacer.acquire()
        before = clock.now
        await pacer.acquire()
        self.assertGreaterEqual(clock.now, 1.0)
        self.assertGreater(clock.now - before, 0.0)

    async def test_retry_after_pushes_the_next_slot_out(self):
        clock = FakeClock()
        pacer = AccountPacer("a", profile(min_interval=0.1), clock=clock,
                             sleeper=clock.sleep)
        await pacer.acquire()
        pacer.impose_retry_after(2.0)
        slept = await pacer.acquire()
        self.assertGreaterEqual(slept, 2.0)

    async def test_retry_after_is_capped_by_the_profile(self):
        clock = FakeClock()
        pacer = AccountPacer("a", profile(min_interval=0.1, retry_after_cap=1.0),
                             clock=clock, sleeper=clock.sleep)
        pacer.impose_retry_after(3600.0)
        slept = await pacer.acquire()
        self.assertLess(slept, 1.5)
        self.assertGreaterEqual(slept, 1.0)

    async def test_negative_retry_after_is_refused(self):
        clock = FakeClock()
        pacer = AccountPacer("a", profile(), clock=clock, sleeper=clock.sleep)
        with self.assertRaises(BudgetError):
            pacer.impose_retry_after(-1)

    def test_blank_account_name_is_refused(self):
        with self.assertRaises(BudgetError):
            AccountPacer("  ", profile())


class RushBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_inflight_cap_is_respected_and_actually_parallel(self):
        # One account is paced serially by design, so cross-account overlap is
        # what exercises the machine-wide cap: 2 accounts, 2 slots each.
        budget = RushBudget(profile(concurrency=4, min_interval=0.05,
                                    window_requests=6000),
                            ["a", "b"], sleeper=asyncio.sleep)
        observed = 0

        async def worker(account: str):
            nonlocal observed
            async with budget.slot(account):
                observed = max(observed, budget.peak_inflight)
                await asyncio.sleep(0.02)

        await asyncio.gather(*[worker(name) for name in ("a", "b") for _ in range(4)])
        self.assertEqual(observed, 2)
        self.assertEqual(budget.total_sent, 8)

    async def test_per_account_share_prevents_one_account_starving_the_rest(self):
        budget = RushBudget(profile(concurrency=4), ["a", "b"], sleeper=asyncio.sleep)
        self.assertEqual(budget.per_account_inflight, 2)
        self.assertEqual(budget.accounts, ("a", "b"))

    async def test_share_is_at_least_one(self):
        budget = RushBudget(profile(concurrency=2), ["a", "b", "c", "d"],
                            sleeper=asyncio.sleep)
        self.assertEqual(budget.per_account_inflight, 1)

    async def test_unknown_account_has_no_slot(self):
        budget = RushBudget(profile(), ["a"], sleeper=asyncio.sleep)
        with self.assertRaises(BudgetError):
            async with budget.slot("ghost"):
                pass

    def test_duplicate_account_names_are_refused(self):
        with self.assertRaises(BudgetError):
            RushBudget(profile(), ["a", "a"])

    def test_no_accounts_is_refused(self):
        with self.assertRaises(BudgetError):
            RushBudget(profile(), [])

    async def test_snapshot_reports_real_counts(self):
        budget = RushBudget(profile(concurrency=2, min_interval=0.05), ["a"],
                            sleeper=asyncio.sleep)
        async with budget.slot("a"):
            pass
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["total_sent"], 1)
        self.assertEqual(snapshot["accounts"]["a"]["sent"], 1)
        self.assertIn("profile", snapshot)


if __name__ == "__main__":
    unittest.main()
