"""Exhaustive FSM transitions and synthetic recovery tests.

No test in this module contacts a university or contains a real endpoint.
"""
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest

from core.course_models import (
    ConflictVerdict, CourseOffering, CourseTarget, ScheduleSnapshot, TimeSlot,
    offering_matches, time_conflict,
)
from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady,
    SubmitReceipt, VerificationPolicy,
)
from core.enrollment_state import (
    ALLOWED_TRANSITIONS, EngineState, EnrollmentFSM, InvalidTransition,
)
from core.preflight import inspect_enrollment_schedule
from tests.test_enrollment import FakeOfficialGateway


TERM = "2026-2027-1"
WEEK_ODD = frozenset((1, 3, 5))
WEEK_EVEN = frozenset((2, 4, 6))
SLOT_ODD = TimeSlot(1, 1, 2, WEEK_ODD)
SLOT_EVEN = TimeSlot(1, 1, 2, WEEK_EVEN)


class FSMContractTests(unittest.TestCase):
    def test_every_declared_transition_is_allowed_and_recorded(self):
        self.assertEqual(set(ALLOWED_TRANSITIONS), set(EngineState))
        for start, destinations in ALLOWED_TRANSITIONS.items():
            for destination in destinations:
                with self.subTest(start=start, destination=destination):
                    fsm = EnrollmentFSM()
                    fsm.state = start
                    fsm.move(destination, "synthetic_event")
                    self.assertEqual(fsm.state, destination)
                    self.assertEqual(len(fsm.history), 1)
                    self.assertEqual(fsm.history[0].before, start)
                    self.assertEqual(fsm.history[0].after, destination)
                    self.assertEqual(fsm.history[0].reason, "synthetic_event")

    def test_all_undeclared_transitions_fail_without_mutation(self):
        for start in EngineState:
            for destination in EngineState:
                if destination in ALLOWED_TRANSITIONS[start]:
                    continue
                with self.subTest(start=start, destination=destination):
                    fsm = EnrollmentFSM()
                    fsm.state = start
                    with self.assertRaises(InvalidTransition):
                        fsm.move(destination, "not_allowed")
                    self.assertEqual(fsm.state, start)
                    self.assertEqual(fsm.history, ())

    def test_reason_codes_must_be_nonempty_and_redacted(self):
        fsm = EnrollmentFSM()
        with self.assertRaises(ValueError):
            fsm.move(EngineState.PREFLIGHT, "")
        with self.assertRaises(ValueError):
            fsm.move(EngineState.PREFLIGHT, "bad\nlog")
        self.assertEqual(fsm.state, EngineState.INITIALIZING)


class DomainAndConflictTests(unittest.TestCase):
    def test_course_class_and_nested_time_data_are_immutable(self):
        target = CourseTarget("A", "C1", "K1", semester=TERM,
                              time_slots=(SLOT_ODD,))
        offering = CourseOffering("C1", "K1", 1, TERM,
                                  time_slots=(SLOT_ODD,))
        with self.assertRaises(FrozenInstanceError):
            target.class_id = "OTHER"
        with self.assertRaises(FrozenInstanceError):
            offering.remain = 99
        with self.assertRaises(ValueError):
            CourseTarget("A", "C1", "K1", time_slots=[SLOT_ODD])
        with self.assertRaises(ValueError):
            CourseOffering("C1", "K1", 1, time_slots=[SLOT_ODD])
        with self.assertRaises(ValueError):
            TimeSlot(1, 1, 2, {1, 2})

    def test_exact_identity_semester_teacher_and_name(self):
        target = CourseTarget("A", "C1", "K1", semester=TERM,
                              course_name="工程制图", teacher="教师甲")
        ok = CourseOffering("C1", "K1", 3, TERM,
                            course_name="工程制图", teacher="教师甲")
        self.assertTrue(offering_matches(target, ok, TERM))
        self.assertFalse(offering_matches(target, replace(ok, class_id="K2"), TERM))
        self.assertFalse(offering_matches(target, replace(ok, semester="OTHER"), TERM))
        self.assertFalse(offering_matches(target, replace(ok, teacher="教师乙"), TERM))
        self.assertFalse(offering_matches(target, replace(ok, course_name="课程乙"), TERM))

    def test_odd_even_weeks_and_intersection(self):
        self.assertEqual(time_conflict((SLOT_ODD,), (SLOT_EVEN,)),
                         ConflictVerdict.CLEAR)
        self.assertEqual(time_conflict((SLOT_ODD,), (SLOT_ODD,)),
                         ConflictVerdict.CONFLICT)
        self.assertEqual(time_conflict((TimeSlot(1, 3, 4, WEEK_ODD),),
                                       (SLOT_ODD,)), ConflictVerdict.CLEAR)
        self.assertEqual(time_conflict((TimeSlot(1, 1, 2),),
                                       (SLOT_ODD,)), ConflictVerdict.UNKNOWN)
        self.assertEqual(time_conflict(None, ()), ConflictVerdict.UNKNOWN)

    def test_enrollment_preflight_is_blocking_for_unknown_or_conflict(self):
        offer = CourseOffering("C1", "K1", 3, TERM,
                               time_slots=(SLOT_ODD,))
        ok = inspect_enrollment_schedule(
            offer, ScheduleSnapshot(TERM, (SLOT_EVEN,), True), TERM)
        self.assertTrue(ok.passed)
        overlap = inspect_enrollment_schedule(
            offer, ScheduleSnapshot(TERM, (SLOT_ODD,), True), TERM)
        self.assertFalse(overlap.passed)
        unknown = inspect_enrollment_schedule(
            replace(offer, time_slots=None),
            ScheduleSnapshot(TERM, (), True), TERM)
        self.assertFalse(unknown.passed)
        unverified = inspect_enrollment_schedule(
            offer, ScheduleSnapshot(TERM, (), False), TERM)
        self.assertFalse(unverified.passed)


class ControlledEnrollmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "attempts.json"
        self.journal = AttemptJournal(self.path, TERM, account_scope="synthetic-account-A")
        self.target = CourseTarget("A", "C1", "K1", semester=TERM)
        self.offer = CourseOffering("C1", "K1", 1, TERM,
                                    time_slots=(SLOT_ODD,))

    def engine(self, gateway, *, max_reads=3):
        return EnrollmentEngine(
            gateway, self.journal,
            user_opted_in=True, institution_permits_write=True,
            verification_policy=VerificationPolicy(max_reads=max_reads,
                                                    interval_seconds=0))

    @staticmethod
    def execute(engine, targets):
        import asyncio
        return asyncio.run(engine.run(targets))

    def test_actual_timetable_overlap_blocks_before_write(self):
        class Busy(FakeOfficialGateway):
            async def read_schedule(self):
                return ScheduleSnapshot(TERM, (SLOT_ODD,), True)
        fake = Busy([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertIn("时间冲突", report.states["A"])
        self.assertFalse(report.halted)
        self.assertEqual(fake.writes, [])
        self.assertEqual(report.transitions["A"][-1].after, EngineState.SKIPPED)

    def test_unknown_schedule_and_wrong_semester_fail_closed(self):
        class Unknown(FakeOfficialGateway):
            async def read_schedule(self):
                return ScheduleSnapshot(TERM, (TimeSlot(1, 1, 2),), True)
        fake = Unknown([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertIn("周次", report.states["A"])
        self.assertEqual(fake.writes, [])
        fake2 = FakeOfficialGateway([self.offer])
        fake2.enrollment_semester = "OTHER"
        report2 = self.execute(self.engine(fake2), [self.target])
        self.assertTrue(report2.halted)
        self.assertEqual(fake2.writes, [])

    def test_timeout_with_official_confirmation_is_success(self):
        class TimeoutAfterCommit(FakeOfficialGateway):
            async def submit_once(self, target):
                self.writes.append(target)
                self.enrolled.add((target.course_id, target.class_id))
                raise TimeoutError("fictional timeout after commit")
        fake = TimeoutAfterCommit([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertFalse(report.halted)
        self.assertIn("确认成功", report.states["A"])
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        self.assertIn(EngineState.VERIFYING,
                      [event.after for event in report.transitions["A"]])

    def test_delayed_visibility_enters_verify_wait_never_resubmits(self):
        class DelayedRecord(FakeOfficialGateway):
            async def read_enrollments(self):
                self.reads += 1
                if self.writes and self.reads < 4:
                    return set()
                return set(self.enrolled)
        fake = DelayedRecord([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertFalse(report.halted)
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        states = [event.after for event in report.transitions["A"]]
        self.assertIn(EngineState.VERIFY_WAIT, states)
        self.assertEqual(states[-1], EngineState.SUCCESS)

    def test_ambiguous_result_survives_restart_and_read_only_recovers(self):
        fake = FakeOfficialGateway([self.offer], result="ambiguous")
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertEqual(self.journal.status(self.target), "pending")
        self.assertEqual(len(fake.writes), 1)
        second = FakeOfficialGateway([self.offer])
        # No record: must not replay the write.
        stopped = self.execute(self.engine(second), [self.target])
        self.assertTrue(stopped.halted)
        self.assertEqual(second.writes, [])
        self.assertIn("既往提交", stopped.states["A"])
        # Official confirmation later: resolve the fence without a write.
        second.enrolled.add(("C1", "K1"))
        recovered = self.execute(self.engine(second), [self.target])
        self.assertFalse(recovered.halted)
        self.assertEqual(self.journal.status(self.target), "confirmed")
        self.assertEqual(second.writes, [])

    def test_legacy_v1_journal_remains_pending_until_confirmed(self):
        fingerprint = self.journal._legacy_fingerprint(self.target)
        self.path.write_text(json.dumps({"version": 1, "attempts": [fingerprint]}),
                             encoding="utf-8")
        self.assertEqual(self.journal.status(self.target), "legacy_pending")
        fake = FakeOfficialGateway([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertEqual(fake.writes, [])

    def test_unlisted_previous_pending_attempt_blocks_new_writes(self):
        old = CourseTarget("OLD", "OLD_C", "OLD_K")
        self.journal.reserve_before_write(old)
        fake = FakeOfficialGateway([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertEqual(fake.writes, [])

    def test_false_receipt_needs_official_read_before_fallback(self):
        class ReadBroken(FakeOfficialGateway):
            async def read_enrollments(self):
                self.reads += 1
                if self.writes:
                    raise OSError("synthetic read failure")
                return set()
        fake = ReadBroken([self.offer], result="reject")
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(self.journal.status(self.target), "pending")

    def test_invalid_journal_refuses_to_submit(self):
        self.path.write_text('{"version":1,"attempts":["not_sha"]}',
                             encoding="utf-8")
        fake = FakeOfficialGateway([self.offer])
        report = self.execute(self.engine(fake), [self.target])
        self.assertTrue(report.halted)
        self.assertEqual(fake.writes, [])


if __name__ == "__main__":
    unittest.main()
