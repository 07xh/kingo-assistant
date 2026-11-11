"""Offline-only enrollment state-machine tests. Never access a university."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady,
    Offering, RunReport, SubmitReceipt, Target,
)


class FakeOfficialGateway:
    official_write_verified = True

    def __init__(self, offerings, *, result="confirm", enrolled=None):
        self.offerings = offerings
        self.enrolled = set(enrolled or ())
        self.result = result
        self.writes = []
        self.reads = 0

    async def read_enrollments(self):
        self.reads += 1
        if self.result == "unreadable" and self.writes:
            raise OSError("simulated network failure")
        return set(self.enrolled)

    async def list_offerings(self):
        return self.offerings

    async def submit_once(self, target):
        self.writes.append(target)
        if self.result == "confirm":
            self.enrolled.add((target.course_id, target.class_id))
            return SubmitReceipt(True)
        if self.result == "reject":
            return SubmitReceipt(False)
        if self.result == "unreadable":
            return SubmitReceipt(True)
        return SubmitReceipt(None)


class AutomaticEnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.json"
        self.journal = AttemptJournal(self.path, "2026-2027-1")
        self.a = Target("A", "COURSE-1", "CLASS-1", "optional")
        self.b = Target("B", "COURSE-2", "CLASS-2", "optional")

    def engine(self, fake, *, opt_in=True, permission=True):
        return EnrollmentEngine(fake, self.journal,
                                user_opted_in=opt_in,
                                institution_permits_write=permission)

    def run_engine(self, engine, targets=None):
        return asyncio.run(engine.run(targets or [self.a]))

    def test_all_gates_required(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        for opt_in, permission, verified in [(False, True, True),
                                              (True, False, True),
                                              (True, True, False)]:
            fake.official_write_verified = verified
            with self.assertRaises(EnrollmentNotReady):
                self.run_engine(self.engine(fake, opt_in=opt_in, permission=permission))
        self.assertEqual(fake.writes, [])
        self.assertFalse(self.path.exists())

    def test_official_record_confirms_not_http_ack(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "官方记录已确认成功")
        self.assertFalse(report.halted)
        self.assertEqual(len(fake.writes), 1)
        # A rerun reads the official record and never submits twice.
        again = self.run_engine(self.engine(fake))
        self.assertEqual(again.states["A"], "已在官方选课记录中")
        self.assertEqual(len(fake.writes), 1)

    def test_ambiguous_submission_never_auto_retries_after_restart(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)], result="unreadable")
        report = self.run_engine(self.engine(fake))
        self.assertTrue(report.halted)
        self.assertIn("不确定", report.states["A"])
        self.assertEqual(len(fake.writes), 1)
        # Restart with a recovered network but NO enrollment record.
        other = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        second = self.run_engine(self.engine(other))
        self.assertTrue(second.halted)
        self.assertIn("既往提交", second.states["A"])
        self.assertEqual(other.writes, [])

    def test_missing_unknown_and_full_never_submit(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", None)])
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "真实余量未知")
        fake.offerings = [Offering("COURSE-1", "CLASS-1", 0)]
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "暂无余量")
        fake.offerings = []
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "未找到唯一教学班")
        self.assertEqual(fake.writes, [])

    def test_alternatives_only_one_when_first_succeeds(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1),
                                    Offering("COURSE-2", "CLASS-2", 1)])
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["B"], "同组志愿已有成功课程")
        self.assertEqual(len(fake.writes), 1)

    def test_explicit_rejection_allows_single_fallback(self):
        class FirstRejected(FakeOfficialGateway):
            async def submit_once(self, target):
                self.writes.append(target)
                if target.key == "A":
                    return SubmitReceipt(False)
                self.enrolled.add((target.course_id, target.class_id))
                return SubmitReceipt(True)
        fake = FirstRejected([Offering("COURSE-1", "CLASS-1", 1),
                              Offering("COURSE-2", "CLASS-2", 1)])
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertFalse(report.halted)
        self.assertEqual(report.states["A"], "官方明确拒绝本次提交")
        self.assertEqual(report.states["B"], "官方记录已确认成功")
        self.assertEqual(len(fake.writes), 2)

    def test_preexisting_enrollment_prevents_new_write(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1),
                                    Offering("COURSE-2", "CLASS-2", 1)],
                                   enrolled={("COURSE-1", "CLASS-1")})
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["B"], "同组志愿已有成功课程")
        self.assertEqual(fake.writes, [])

    def test_duplicate_and_missing_exact_class_rejected(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        with self.assertRaises(ValueError):
            self.run_engine(self.engine(fake), [self.a, self.a])
        with self.assertRaises(ValueError):
            self.run_engine(self.engine(fake), [Target("X", "COURSE-1", "")])
        self.assertEqual(fake.writes, [])

    def test_existing_run_lock_blocks_second_instance(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        with self.journal.exclusive():
            with self.assertRaises(EnrollmentNotReady):
                self.run_engine(self.engine(fake))
        self.assertEqual(fake.writes, [])

    def test_unknown_ack_without_official_record_halts(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)], result="ambiguous")
        report = self.run_engine(self.engine(fake))
        self.assertTrue(report.halted)
        self.assertIn("未确认", report.states["A"])
        self.assertEqual(len(fake.writes), 1)


if __name__ == "__main__":
    unittest.main()
