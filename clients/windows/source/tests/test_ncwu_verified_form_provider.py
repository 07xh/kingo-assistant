"""Offline tests for current-session form source boundary. No campus requests."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest

from core.attestation import AttestationRegistry
from core.course_models import CourseTarget
from core.enrollment import EnrollmentNotReady
from core.ncwu_live_elective import ORIGIN
from core.ncwu_submission_transport import SUBMIT_PATH
from core.ncwu_verified_form_provider import (
    CurrentS202FormObservation, VerifiedS202FormProvider,
)
from core.ncwu_verified_readback_bridge import ReadbackScope

TERM = "2026-2027-1"
EVIDENCE = b"fictional reviewed item to internal class mapping"
FIELDS = (
    ("xktype", "2"), ("xh", "FAKE-OWNER"), ("xn", "2026"),
    ("xq", "0"), ("lcid", "FAKE-ROUND"), ("hidOption", "TEST"),
    ("items", "TEST-ITEM"),
)


def scope(**changes) -> ReadbackScope:
    values = dict(
        account_scope="fake-account", semester=TERM, school_origin=ORIGIN,
        internal_student_id="FAKE-OWNER", academic_year="2026",
        academic_term="0", round_id="FAKE-ROUND",
        session_proof="JSESSIONID=SENSITIVE_TEST_SESSION",
    )
    values.update(changes)
    return ReadbackScope(**values)


class VerifiedFormProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.document = root / "reviewed-contract.txt"
        self.document.write_text("synthetic reviewed contract", encoding="utf-8")
        self.attestations = AttestationRegistry(entries={}, root=root).with_entry(
            AttestationRegistry.issue(
                root, gate="official_write_verified",
                evidence="reviewed-contract.txt", reviewer="offline-test",
            )
        )
        self.current = scope()
        self.target = CourseTarget(
            "first", "COURSE-A", "SKBJDM-A", semester=TERM)
        self.calls = 0

    def observation(self, **changes) -> CurrentS202FormObservation:
        data = dict(
            scope=self.current, source_path=SUBMIT_PATH,
            course_code="COURSE-A", internal_class_id="SKBJDM-A",
            selected_item="TEST-ITEM", fields=FIELDS,
            binding_evidence=EVIDENCE,
            observed_monotonic=time.monotonic(),
        )
        data.update(changes)
        return CurrentS202FormObservation(**data)

    def provider(self, reader=None, *, attestation=None, opted=True, permitted=True):
        async def default_reader(target, current):
            self.calls += 1
            self.assertEqual(target, self.target)
            self.assertEqual(current, self.current)
            return self.observation()
        return VerifiedS202FormProvider(
            account_scope="fake-account", enrollment_semester=TERM,
            attestations=self.attestations if attestation is None else attestation,
            current_scope=lambda: self.current,
            read_current_form=reader or default_reader,
            user_opted_in=opted,
            institution_permits_write=permitted,
        )

    def test_fictional_current_form_builds_one_prepared_packet_without_network(self):
        prepared, evidence = asyncio.run(self.provider()(self.target))
        self.assertEqual(self.calls, 1)
        self.assertEqual(prepared.target_teaching_class_id, "SKBJDM-A")
        self.assertEqual(prepared.fields, FIELDS)
        self.assertEqual(prepared.item_binding_sha256, sha256(EVIDENCE).hexdigest())
        self.assertEqual(evidence, EVIDENCE)
        self.assertNotIn("SENSITIVE_TEST_SESSION", repr(self.observation()))
        self.assertNotIn("FAKE-OWNER", repr(self.observation()))
        self.assertNotIn("FAKE-OWNER", repr(prepared))

    def test_no_live_permission_or_user_opt_in_means_no_reader(self):
        for opted, permitted in ((False, True), (True, False), (False, False)):
            with self.subTest(opted=opted, permitted=permitted):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.provider(opted=opted, permitted=permitted)(
                        self.target))
        self.assertEqual(self.calls, 0)

    def test_missing_write_attestation_blocks_before_reader(self):
        empty = AttestationRegistry(entries={}, root=self.attestations.root)
        with self.assertRaises(Exception):
            asyncio.run(self.provider(attestation=empty)(self.target))
        self.assertEqual(self.calls, 0)

    def test_wrong_target_or_school_path_blocks(self):
        async def wrong_target(_, __):
            return self.observation(internal_class_id="WRONG-CLASS")
        async def wrong_path(_, __):
            return self.observation(source_path="/hsjw/unknown.action")
        for reader in (wrong_target, wrong_path):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.provider(reader)(self.target))

    def test_display_class_never_substitutes_for_internal_class(self):
        async def visible_number(_, __):
            return self.observation(internal_class_id="VISIBLE-SKBH")
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.provider(visible_number)(self.target))

    def test_rotating_session_during_await_blocks(self):
        async def changed(_, current):
            observed = self.observation(scope=current)
            self.current = scope(session_proof="JSESSIONID=NEW_SESSION")
            return observed
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.provider(changed)(self.target))

    def test_stale_and_future_observations_block(self):
        async def stale(_, __):
            return self.observation(observed_monotonic=time.monotonic() - 10)
        async def future(_, __):
            return self.observation(observed_monotonic=time.monotonic() + 10)
        for reader in (stale, future):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.provider(reader)(self.target))

    def test_wrong_scope_and_missing_proof_block(self):
        async def changed_account(_, __):
            return self.observation(scope=scope(account_scope="another"))
        async def missing_proof(_, __):
            return self.observation(binding_evidence=b"")
        async def malformed_items(_, __):
            return self.observation(selected_item="OTHER-ITEM")
        for reader in (changed_account, missing_proof, malformed_items):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.provider(reader)(self.target))

    def test_replaced_attestation_during_await_blocks(self):
        async def tampered(_, __):
            snapshot = self.observation()
            self.document.write_text("tampered", encoding="utf-8")
            return snapshot
        with self.assertRaises(Exception):
            asyncio.run(self.provider(tampered)(self.target))

    def test_wrong_semester_before_reader_blocks(self):
        other = CourseTarget("second", "COURSE-A", "SKBJDM-A", semester="2026-2027-2")
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.provider()(other))
        self.assertEqual(self.calls, 0)

    def test_reject_unscoped_or_forged_reader_result(self):
        async def candidate(_, __):
            return {"items": "TEST-ITEM"}
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.provider(candidate)(self.target))


if __name__ == "__main__":
    unittest.main()
