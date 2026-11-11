"""Offline-only tests for automatic enrollment safety and priority flow."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady,
    Offering, SubmitReceipt, Target, WindowState,
)


class FakeOfficialGateway:
    official_catalog_verified = True
    official_enrollment_verified = True
    official_window_verified = True
    official_write_verified = True
    account_scope = "synthetic-account-A"

    def __init__(self, offerings, *, result="confirm", enrolled=None,
                 window_states=None, responses=None, offering_snapshots=None):
        self.offerings = offerings
        self.offering_snapshots = list(offering_snapshots or [])
        self.enrolled = set(enrolled or ())
        self.result = result
        self.responses = dict(responses or {})
        self.window_states = list(window_states or [WindowState.OPEN])
        self.window_calls = 0
        self.catalog_calls = 0
        self.writes = []
        self.reads = 0

    async def selection_window(self):
        index = min(self.window_calls, len(self.window_states) - 1)
        self.window_calls += 1
        return self.window_states[index]

    async def read_enrollments(self):
        self.reads += 1
        if self.result == "unreadable" and self.writes:
            raise OSError("simulated network failure")
        return set(self.enrolled)

    async def list_offerings(self):
        index = self.catalog_calls
        self.catalog_calls += 1
        if self.offering_snapshots:
            return self.offering_snapshots[min(index, len(self.offering_snapshots) - 1)]
        return self.offerings

    async def submit_once(self, target):
        self.writes.append(target)
        if target.key in self.responses:
            accepted = self.responses[target.key]
            if accepted is True:
                self.enrolled.add((target.course_id, target.class_id))
            return SubmitReceipt(accepted)
        if self.result == "confirm":
            self.enrolled.add((target.course_id, target.class_id))
            return SubmitReceipt(True)
        if self.result == "reject":
            return SubmitReceipt(False)
        if self.result == "unreadable":
            return SubmitReceipt(True)
        if self.result == "ambiguous":
            return SubmitReceipt(None)
        return SubmitReceipt(None)


class AutomaticEnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.json"
        self.journal = AttemptJournal(self.path, "2026-2027-1")
        self.a = Target("A", "COURSE-1", "CLASS-1", "optional")
        self.b = Target("B", "COURSE-2", "CLASS-2", "optional")

    def engine(self, fake, *, opt_in=True, permission=True, journal=None):
        return EnrollmentEngine(fake, journal or self.journal,
                                user_opted_in=opt_in,
                                institution_permits_write=permission)

    def run_engine(self, engine, targets=None):
        return asyncio.run(engine.run(targets or [self.a]))

    def test_all_independent_capability_gates_required(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        capabilities = [
            "official_catalog_verified", "official_enrollment_verified",
            "official_window_verified", "official_write_verified",
        ]
        for field in capabilities:
            setattr(fake, field, False)
            with self.assertRaises(EnrollmentNotReady):
                self.run_engine(self.engine(fake))
            setattr(fake, field, True)
        with self.assertRaises(EnrollmentNotReady):
            self.run_engine(self.engine(fake, opt_in=False))
        with self.assertRaises(EnrollmentNotReady):
            self.run_engine(self.engine(fake, permission=False))
        self.assertEqual(fake.writes, [])

    def test_closed_or_unknown_official_window_never_writes(self):
        for state, expected in ((WindowState.CLOSED, "学校选课窗口未开放"),
                                (WindowState.UNKNOWN, "无法确认学校选课窗口")):
            fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)],
                                       window_states=[state])
            report = self.run_engine(self.engine(fake))
            self.assertTrue(report.halted)
            self.assertEqual(report.states["A"], expected)
            self.assertEqual(fake.catalog_calls, 0)
            self.assertEqual(fake.writes, [])

    def test_official_record_confirms_not_http_ack(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "官方记录已确认成功")
        self.assertFalse(report.halted)
        self.assertEqual(len(fake.writes), 1)
        self.assertEqual(self.journal.status(self.a), "confirmed")
        again = self.run_engine(self.engine(fake))
        self.assertEqual(again.states["A"], "已在官方选课记录中")
        self.assertEqual(len(fake.writes), 1)

    def test_ambiguous_submission_never_auto_retries_after_restart(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)], result="unreadable")
        report = self.run_engine(self.engine(fake))
        self.assertTrue(report.halted)
        self.assertIn("不确定", report.states["A"])
        self.assertEqual(self.journal.status(self.a), "pending")
        self.assertEqual(len(fake.writes), 1)
        other = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        second = self.run_engine(self.engine(other))
        self.assertTrue(second.halted)
        self.assertIn("未核实提交", second.states["A"])
        self.assertEqual(other.writes, [])

    def test_pending_is_reconciled_if_official_record_later_appears(self):
        first = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)], result="unreadable")
        self.run_engine(self.engine(first))
        recovered = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)],
                                         enrolled={("COURSE-1", "CLASS-1")})
        report = self.run_engine(self.engine(recovered))
        self.assertEqual(report.states["A"], "已在官方选课记录中")
        self.assertEqual(self.journal.status(self.a), "confirmed")
        self.assertEqual(recovered.writes, [])

    def test_confirmed_journal_mismatch_halts_instead_of_resubmitting(self):
        first = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        self.run_engine(self.engine(first))
        missing = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 2)])
        report = self.run_engine(self.engine(missing))
        self.assertTrue(report.halted)
        self.assertIn("不一致", report.states["A"])
        self.assertEqual(missing.writes, [])

    def test_explicit_rejection_allows_fallback_and_persists_state(self):
        fake = FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 1)],
            responses={"A": False},
        )
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertFalse(report.halted)
        self.assertEqual(report.states["A"], "官方明确拒绝本次提交")
        self.assertEqual(report.states["B"], "官方记录已确认成功")
        self.assertEqual(self.journal.status(self.a), "rejected")
        self.assertEqual(self.journal.status(self.b), "confirmed")
        self.assertEqual(len(fake.writes), 2)
        restarted = FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 1)],
            enrolled={("COURSE-2", "CLASS-2")},
        )
        second = self.run_engine(self.engine(restarted), [self.a, self.b])
        self.assertEqual(second.states["A"], "此前已明确拒绝，跳过重复提交")
        self.assertEqual(second.states["B"], "已在官方选课记录中")
        self.assertEqual(restarted.writes, [])

    def test_catalog_refreshes_after_explicit_rejection(self):
        fake = FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 1)],
            responses={"A": False},
            offering_snapshots=[
                [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 1)],
                [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 0)],
            ],
        )
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["B"], "暂无余量")
        self.assertEqual(fake.catalog_calls, 2)
        self.assertEqual([x.key for x in fake.writes], ["A"])

    def test_selection_window_closing_between_alternatives_stops_fallback(self):
        fake = FakeOfficialGateway(
            [Offering("COURSE-1", "CLASS-1", 1), Offering("COURSE-2", "CLASS-2", 1)],
            responses={"A": False},
            window_states=[WindowState.OPEN, WindowState.OPEN, WindowState.CLOSED],
        )
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertTrue(report.halted)
        self.assertEqual(report.states["A"], "官方明确拒绝本次提交")
        self.assertEqual(report.states["B"], "学校选课窗口已关闭")
        self.assertEqual([x.key for x in fake.writes], ["A"])

    def test_group_alternatives_only_one_when_first_succeeds(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1),
                                    Offering("COURSE-2", "CLASS-2", 1)])
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["B"], "同组志愿已有成功课程")
        self.assertEqual(len(fake.writes), 1)

    def test_preexisting_enrollment_prevents_new_write(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1),
                                    Offering("COURSE-2", "CLASS-2", 1)],
                                   enrolled={("COURSE-1", "CLASS-1")})
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["B"], "同组志愿已有成功课程")
        self.assertEqual(fake.writes, [])

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

    def test_duplicate_targets_and_missing_class_are_rejected(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        with self.assertRaises(ValueError):
            self.run_engine(self.engine(fake), [self.a, self.a])
        with self.assertRaises(ValueError):
            self.run_engine(self.engine(fake), [Target("X", "COURSE-1", "")])
        with self.assertRaises(ValueError):
            self.run_engine(self.engine(fake), [Target(" X ", "COURSE-1", "CLASS-1")])
        self.assertEqual(fake.writes, [])

    def test_invalid_catalog_data_fails_closed(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", -1)])
        report = self.run_engine(self.engine(fake))
        self.assertTrue(report.halted)
        self.assertEqual(report.states["A"], "无法确认官方状态")
        self.assertEqual(fake.writes, [])

    def test_existing_run_lock_blocks_second_instance_and_releases(self):
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        with self.journal.exclusive():
            with self.assertRaises(EnrollmentNotReady):
                self.run_engine(self.engine(fake))
        self.assertTrue(self.journal.lock_path.exists())
        # A persistent lock file is expected; the OS lock, not file existence,
        # determines whether another process is still running.
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "官方记录已确认成功")

    def test_orphaned_lock_file_is_reusable_after_process_exit(self):
        self.journal.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal.lock_path.write_bytes(b"\0")
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)])
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "官方记录已确认成功")

    def test_account_scope_isolated_without_persisting_account_identifier(self):
        first = AttemptJournal(self.path, "2026-2027-1", account_scope="synthetic-A")
        second = AttemptJournal(self.path, "2026-2027-1", account_scope="synthetic-B")
        first.reserve_before_write(self.a)
        self.assertEqual(first.status(self.a), "pending")
        self.assertIsNone(second.status(self.a))
        content = self.path.read_text(encoding="utf-8")
        self.assertNotIn("synthetic-A", content)
        self.assertNotIn("COURSE-1", content)
        self.assertNotIn("CLASS-1", content)

    def test_v1_journal_migrates_to_unresolved_legacy_state(self):
        old_fingerprint = self.journal._legacy_fingerprint(self.a)
        self.path.write_text(json.dumps({"version": 1, "attempts": [old_fingerprint]}),
                             encoding="utf-8")
        self.assertEqual(self.journal.status(self.a), "legacy_pending")
        migrated = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["legacy_attempts"], [old_fingerprint])

    def test_corrupt_journal_fails_closed(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.journal.status(self.a)


    def test_legacy_pending_is_resolved_when_official_enrollment_appears(self):
        old = self.journal._legacy_fingerprint(self.a)
        self.path.write_text(json.dumps({"version": 1, "attempts": [old]}), encoding="utf-8")
        fake = FakeOfficialGateway([Offering("COURSE-1", "CLASS-1", 1)],
                                   enrolled={("COURSE-1", "CLASS-1")})
        report = self.run_engine(self.engine(fake))
        self.assertEqual(report.states["A"], "已在官方选课记录中")
        self.assertEqual(self.journal.status(self.a), "confirmed")

    def test_existing_course_in_another_class_satisfies_its_alternative_group(self):
        fake = FakeOfficialGateway(
            [Offering("COURSE-2", "CLASS-2", 1)],
            enrolled={("COURSE-1", "OTHER-CLASS")},
        )
        report = self.run_engine(self.engine(fake), [self.a, self.b])
        self.assertEqual(report.states["A"], "该课程已有其他教学班，跳过")
        self.assertEqual(report.states["B"], "同组志愿已有成功课程")
        self.assertEqual(fake.writes, [])


if __name__ == "__main__":
    unittest.main()
