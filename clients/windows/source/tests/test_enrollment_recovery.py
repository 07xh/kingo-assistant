"""Offline-only tests for single-read recovery independent of write/window.

Synthetic gateway only: no university URL, credentials or network calls.
"""
import asyncio
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import tempfile
import unittest

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady,
    Offering, Target, WindowState,
)
from core.enrollment_recovery import (
    RecoveryEntry, RecoveryReport, RecoveryStatus,
)
from tests.test_enrollment import FakeOfficialGateway


TERM = "2026-2027-1"


class ReadonlyRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "attempts.json"
        self.journal = AttemptJournal(
            self.path, TERM, account_scope="synthetic-account-A")
        self.target = Target("A", "COURSE-1", "CLASS-1", semester=TERM)
        self.other = Target("B", "COURSE-2", "CLASS-2", semester=TERM)

    def make_gateway(self, *, enrolled=()):
        return FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1)],
            enrolled=set(enrolled),
            window_states=[WindowState.CLOSED],
        )

    def reconcile(self, gateway, targets=None):
        engine = EnrollmentEngine(
            gateway, self.journal,
            user_opted_in=False,
            institution_permits_write=False,
        )
        return asyncio.run(engine.reconcile_pending(targets or [self.target]))

    def test_confirmed_outside_window_with_no_write_authorization(self):
        self.journal.reserve_before_write(self.target)
        gateway = self.make_gateway(enrolled={("COURSE-1", "CLASS-1")})
        gateway.official_window_verified = False
        gateway.official_write_verified = False
        result = self.reconcile(gateway)
        self.assertTrue(result.official_read_ok)
        self.assertFalse(result.needs_attention)
        self.assertEqual(result.states["A"], RecoveryStatus.CONFIRMED)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        self.assertEqual(gateway.writes, [])
        self.assertEqual(gateway.window_calls, 0)
        self.assertEqual(gateway.catalog_calls, 0)
        self.assertEqual(gateway.reads, 1)

    def test_pending_remains_protected_on_official_absence(self):
        self.journal.reserve_before_write(self.target)
        initial = self.path.read_bytes()
        gateway = self.make_gateway()
        result = self.reconcile(gateway)
        self.assertTrue(result.needs_attention)
        self.assertEqual(result.unresolved_keys, ("A",))
        self.assertEqual(result.states["A"], RecoveryStatus.PENDING)
        self.assertEqual(self.journal.status(self.target), "pending")
        self.assertEqual(self.path.read_bytes(), initial)
        self.assertEqual(gateway.writes, [])

    def test_broken_read_does_not_mutate_pending_state(self):
        class Broken(FakeOfficialGateway):
            async def read_enrollments(self):
                self.reads += 1
                raise OSError("simulated read failure")

        self.journal.reserve_before_write(self.target)
        initial = self.path.read_bytes()
        gateway = Broken([], window_states=[WindowState.CLOSED])
        result = self.reconcile(gateway)
        self.assertFalse(result.official_read_ok)
        self.assertEqual(result.states["A"], RecoveryStatus.UNAVAILABLE)
        self.assertTrue(result.needs_attention)
        self.assertEqual(self.path.read_bytes(), initial)
        self.assertEqual(gateway.writes, [])
        self.assertEqual(gateway.window_calls, 0)

    def test_malformed_readback_never_confirms(self):
        class Invalid(FakeOfficialGateway):
            async def read_enrollments(self):
                self.reads += 1
                return [("COURSE-1", "CLASS-1")]  # list is not an official set

        self.journal.reserve_before_write(self.target)
        gateway = Invalid([], window_states=[WindowState.CLOSED])
        result = self.reconcile(gateway)
        self.assertFalse(result.official_read_ok)
        self.assertEqual(result.states["A"], RecoveryStatus.UNAVAILABLE)
        self.assertEqual(self.journal.status(self.target), "pending")

    def test_v1_legacy_attempt_can_be_confirmed_without_write(self):
        fingerprint = self.journal._legacy_fingerprint(self.target)
        self.path.write_text(
            json.dumps({"version": 1, "attempts": [fingerprint]}),
            encoding="utf-8",
        )
        gateway = self.make_gateway(enrolled={("COURSE-1", "CLASS-1")})
        result = self.reconcile(gateway)
        self.assertEqual(result.states["A"], RecoveryStatus.CONFIRMED)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        self.assertEqual(gateway.writes, [])
        migrated = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertNotIn(fingerprint, migrated["legacy_attempts"])

    def test_rejected_attempt_can_only_become_confirmed_with_official_record(self):
        self.journal.reserve_before_write(self.target)
        self.journal.record_rejected(self.target)
        gateway = self.make_gateway()
        absent = self.reconcile(gateway)
        self.assertEqual(absent.states["A"], RecoveryStatus.REJECTED)
        self.assertEqual(self.journal.status(self.target), "rejected")
        gateway.enrolled.add(("COURSE-1", "CLASS-1"))
        present = self.reconcile(gateway)
        self.assertEqual(present.states["A"], RecoveryStatus.CONFIRMED)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        self.assertEqual(gateway.writes, [])

    def test_historical_confirmation_mismatch_stays_blocked(self):
        self.journal.reserve_before_write(self.target)
        self.journal.record_confirmed(self.target)
        gateway = self.make_gateway()
        report = self.reconcile(gateway)
        self.assertEqual(report.states["A"], RecoveryStatus.CONFIRMED_MISMATCH)
        self.assertTrue(report.needs_attention)
        self.assertEqual(self.journal.status(self.target), "confirmed")

    def test_preexisting_official_selection_without_local_attempt(self):
        gateway = self.make_gateway(enrolled={("COURSE-1", "CLASS-1")})
        report = self.reconcile(gateway)
        self.assertEqual(report.states["A"], RecoveryStatus.ALREADY_SELECTED)
        self.assertIsNone(self.journal.status(self.target))
        self.assertFalse(report.needs_attention)
        self.assertEqual(gateway.writes, [])

    def test_unattempted_target_is_not_a_failure(self):
        gateway = self.make_gateway()
        report = self.reconcile(gateway)
        self.assertEqual(report.states["A"], RecoveryStatus.NOT_ATTEMPTED)
        self.assertFalse(report.needs_attention)
        self.assertEqual(gateway.writes, [])

    def test_orphaned_pending_is_reported_without_guessing_identity(self):
        self.journal.reserve_before_write(self.other)
        self.journal.reserve_before_write(self.target)
        gateway = self.make_gateway(enrolled={("COURSE-1", "CLASS-1")})
        report = self.reconcile(gateway, [self.target])
        self.assertEqual(report.states["A"], RecoveryStatus.CONFIRMED)
        self.assertEqual(report.unlisted_pending, 1)
        self.assertTrue(report.needs_attention)
        self.assertEqual(self.journal.status(self.other), "pending")
        self.assertEqual(gateway.writes, [])

    def test_mixed_target_states_return_in_user_order(self):
        self.journal.reserve_before_write(self.target)
        gateway = self.make_gateway()
        result = self.reconcile(gateway, [self.other, self.target])
        self.assertEqual(
            tuple(item.key for item in result.entries), ("B", "A"))
        self.assertEqual(result.states, {
            "B": RecoveryStatus.NOT_ATTEMPTED,
            "A": RecoveryStatus.PENDING,
        })

    def test_wrong_account_or_semester_block_before_read(self):
        gateway = self.make_gateway()
        gateway.account_scope = "other-account"
        with self.assertRaises(EnrollmentNotReady):
            self.reconcile(gateway)
        self.assertEqual(gateway.reads, 0)
        gateway.account_scope = "synthetic-account-A"
        gateway.enrollment_semester = "another-term"
        with self.assertRaises(EnrollmentNotReady):
            self.reconcile(gateway)
        self.assertEqual(gateway.reads, 0)

    def test_readback_capability_required_without_write_gate(self):
        gateway = self.make_gateway()
        gateway.official_enrollment_verified = False
        with self.assertRaises(EnrollmentNotReady):
            self.reconcile(gateway)
        self.assertEqual(gateway.reads, 0)
        self.assertEqual(gateway.writes, [])

    def test_invalid_target_semester_blocks_before_read(self):
        gateway = self.make_gateway()
        with self.assertRaises(ValueError):
            self.reconcile(gateway, [Target("X", "C", "K", semester="wrong")])
        self.assertEqual(gateway.reads, 0)

    def test_existing_os_lock_stops_concurrent_recovery(self):
        gateway = self.make_gateway()
        with self.journal.exclusive():
            with self.assertRaises(EnrollmentNotReady):
                self.reconcile(gateway)
        self.assertEqual(gateway.reads, 0)

    def test_corrupt_journal_fails_closed_without_school_read(self):
        self.path.write_text("{not-valid-json", encoding="utf-8")
        gateway = self.make_gateway()
        with self.assertRaises(ValueError):
            self.reconcile(gateway)
        self.assertEqual(gateway.reads, 0)

    def test_report_is_immutable_and_contains_no_course_identity(self):
        report = RecoveryReport(
            (RecoveryEntry("A", RecoveryStatus.PENDING),),
            official_read_ok=True, unlisted_pending=0,
        )
        with self.assertRaises(FrozenInstanceError):
            report.official_read_ok = False
        self.assertEqual(report.unresolved_keys, ("A",))
        self.assertNotIn("COURSE", repr(report))


if __name__ == "__main__":
    unittest.main()
