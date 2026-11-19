"""Offline regressions for optional pre-window preparation and deferred readback.

Only synthetic gateways are used. No university endpoints or real credentials.
"""
import asyncio
from pathlib import Path
import tempfile
import unittest

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, Offering, Target, VerificationPolicy,
)
from core.prepared_preflight import prepare_schedule
from tests.attestation_support import valid_registry
from tests.test_enrollment import FakeOfficialGateway


TERM = "2026-2027-1"


class PreflightAndDeferredTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Target("A", "COURSE-1", "CLASS-1", semester=TERM)
        self.journal = AttemptJournal(
            Path(self.tmp.name) / "journal.json", TERM,
            account_scope="synthetic-account-A",
        )

    def make_engine(self, gateway):
        return EnrollmentEngine(
            gateway, self.journal, user_opted_in=True,
            institution_permits_write=True, attestations=valid_registry(),
            verification_policy=VerificationPolicy(max_reads=3, interval_seconds=0),
        )

    def gateway(self, *, result="confirm"):
        return FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1)],
            result=result,
        )

    async def test_preopen_snapshot_reuses_only_verified_timetable(self):
        gateway = self.gateway()
        gateway.preopen_schedule_permitted = True
        gateway.preflight_session_token = lambda: ("in-memory-session", TERM)
        original = gateway.read_schedule
        calls = []

        async def counted():
            calls.append("read")
            return await original()

        gateway.read_schedule = counted
        engine = self.make_engine(gateway)
        prepared = await prepare_schedule(engine, [self.a])
        self.assertIsNotNone(prepared)
        self.assertEqual(len(calls), 1)
        self.assertEqual(gateway.catalog_calls, 0)
        report = await engine.run([self.a], prepared_schedule=prepared)
        self.assertFalse(report.halted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(gateway.catalog_calls, 1)  # Never cached seats.
        self.assertEqual(gateway.reads, 2)  # Fresh pre-submit + result read.
        self.assertEqual(len(gateway.writes), 1)

    async def test_changed_session_forces_fresh_schedule(self):
        gateway = self.gateway()
        gateway.preopen_schedule_permitted = True
        session = ["first"]
        gateway.preflight_session_token = lambda: tuple(session)
        original = gateway.read_schedule
        reads = []

        async def counted():
            reads.append("schedule")
            return await original()

        gateway.read_schedule = counted
        engine = self.make_engine(gateway)
        prepared = await prepare_schedule(engine, [self.a])
        session[:] = ["second"]
        self.assertFalse(prepared.usable(gateway, [self.a]))
        result = await engine.run([self.a], prepared_schedule=prepared)
        self.assertFalse(result.halted)
        self.assertEqual(len(reads), 2)

    async def test_without_preopen_permission_never_prefetches(self):
        gateway = self.gateway()
        engine = self.make_engine(gateway)
        self.assertIsNone(await prepare_schedule(engine, [self.a]))
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(gateway.reads, 0)

    async def test_terminal_submit_returns_before_readback_but_resolves(self):
        gateway = self.gateway()
        engine = self.make_engine(gateway)
        preliminary = await engine.run([self.a], defer_readback=True)
        self.assertTrue(preliminary.halted)
        self.assertIn("等待", preliminary.states["A"])
        self.assertTrue(engine.has_deferred_verification)
        final = await engine.finish_deferred_verification()
        self.assertFalse(final.halted)
        self.assertEqual(final.states["A"], "官方记录已确认成功")
        self.assertFalse(engine.has_deferred_verification)
        self.assertEqual(len(gateway.writes), 1)
        self.assertEqual(self.journal.status(self.a), "confirmed")

    async def test_unreadable_result_stays_pending_never_resends(self):
        gateway = self.gateway(result="unreadable")
        engine = self.make_engine(gateway)
        preliminary = await engine.run([self.a], defer_readback=True)
        self.assertIn("等待", preliminary.states["A"])
        final = await engine.finish_deferred_verification()
        self.assertTrue(final.halted)
        self.assertEqual(self.journal.status(self.a), "pending")
        self.assertEqual(len(gateway.writes), 1)
        self.assertIn("不确定", final.states["A"])

    async def test_explicit_rejection_is_persisted_not_retried(self):
        gateway = self.gateway(result="reject")
        engine = self.make_engine(gateway)
        await engine.run([self.a], defer_readback=True)
        final = await engine.finish_deferred_verification()
        self.assertFalse(final.halted)
        self.assertEqual(self.journal.status(self.a), "rejected")
        self.assertEqual(len(gateway.writes), 1)
        self.assertIn("拒绝", final.states["A"])


if __name__ == "__main__":
    unittest.main()
