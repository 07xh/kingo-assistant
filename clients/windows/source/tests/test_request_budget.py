"""Offline, network-free policy and shared-budget regression tests."""
import asyncio
from pathlib import Path
import tempfile
import time
import unittest

from core.request_budget import (
    GlobalRequestBudget, PolicyError, RequestPolicy, SingleFlight, WindowLimit,
)


class RequestBudgetTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "quota.db"

    def budget(self, windows=((0.08, 1),), *, verified=True, concurrency=1):
        policy = RequestPolicy(tuple(WindowLimit(*w) for w in windows),
                               concurrency, "synthetic-test-policy", verified)
        return GlobalRequestBudget(policy, self.db)

    async def test_unverified_policy_rejects(self):
        budget = self.budget(verified=False)
        with self.assertRaises(PolicyError):
            async with budget.request():
                self.fail("request was permitted")

    async def test_multi_window_enforced(self):
        budget = self.budget(((0.03, 2), (0.14, 2)))
        start = time.monotonic()
        for _ in range(3):
            async with budget.request():
                pass
        self.assertGreaterEqual(time.monotonic() - start, 0.125)

    async def test_cross_instance_shared_reservation(self):
        first = self.budget()
        second = self.budget()
        start = time.monotonic()
        async with first.request():
            pass
        async with second.request():
            pass
        self.assertGreaterEqual(time.monotonic() - start, 0.070)

    async def test_retry_after_persists_on_restart(self):
        first = self.budget(((0.01, 10),))
        await first.impose_retry_after(0.09)
        restarted = self.budget(((0.01, 10),))
        start = time.monotonic()
        async with restarted.request():
            pass
        self.assertGreaterEqual(time.monotonic() - start, 0.075)

    async def test_concurrent_request_slot_is_serial(self):
        budget = self.budget(((0.01, 10),))
        events = []
        async def worker(n):
            async with budget.request():
                events.append(f"start-{n}")
                await asyncio.sleep(0.02)
                events.append(f"end-{n}")
        await asyncio.gather(worker(1), worker(2))
        self.assertEqual(events, ["start-1", "end-1", "start-2", "end-2"])

    async def test_concurrent_managers_share_one_request_lease(self):
        first = self.budget(((0.01, 10),))
        second = self.budget(((0.01, 10),))
        started = asyncio.Event()
        release = asyncio.Event()
        overlap = []

        async def hold_first():
            async with first.request():
                started.set()
                await release.wait()

        async def acquire_second():
            async with second.request():
                overlap.append('second')

        one = asyncio.create_task(hold_first())
        await started.wait()
        two = asyncio.create_task(acquire_second())
        await asyncio.sleep(0.08)
        self.assertEqual(overlap, [])
        release.set()
        await asyncio.gather(one, two)
        self.assertEqual(overlap, ['second'])

    async def test_process_lease_released_after_request_exception(self):
        first = self.budget(((0.01, 10),))
        second = self.budget(((0.01, 10),))
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            async with first.request():
                raise RuntimeError('synthetic')
        async with asyncio.timeout(2):
            async with second.request():
                pass

    async def test_policy_change_requires_reconciliation(self):
        self.budget()
        with self.assertRaises(PolicyError):
            self.budget(((0.08, 2),))

    async def test_validation(self):
        with self.assertRaises(PolicyError):
            WindowLimit(0, 1)
        with self.assertRaises(PolicyError):
            WindowLimit(1, 0)
        with self.assertRaises(PolicyError):
            self.budget(concurrency=0)
        with self.assertRaises(PolicyError):
            await self.budget().impose_retry_after(-1)

    async def test_single_flight_coalesces_and_preserves_cancellation(self):
        sf = SingleFlight()
        calls = []
        started = asyncio.Event()
        release = asyncio.Event()
        async def factory():
            calls.append(1)
            started.set()
            await release.wait()
            return 42
        one = asyncio.create_task(sf.run("same", factory))
        await started.wait()
        two = asyncio.create_task(sf.run("same", factory))
        await asyncio.sleep(0)
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await one
        release.set()
        self.assertEqual(await two, 42)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
