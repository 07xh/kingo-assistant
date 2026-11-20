"""Offline tests for the optional proof-scoped NCWU exact readback bridge."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import time
import unittest

from core.attestation import AttestationRegistry
from core.enrollment import EnrollmentNotReady
from core.ncwu_exact_readback import ExactSelectedSnapshot
from core.ncwu_live_elective import ORIGIN
from core.ncwu_s202_readback import S202ReadbackResult
from core.ncwu_verified_readback_bridge import (
    ReadbackScope, VerifiedS202ReadbackBridge,
)

PATH = "/hsjw/student/approved-exact-synthetic.jsp"
TERM = "2026-2027-1"
RECORD = ("COURSE-A", "SKBJDM-A")


def scope(**changes):
    data = dict(
        account_scope="fictional-owner", semester=TERM,
        school_origin=ORIGIN, internal_student_id="FAKE-ID",
        academic_year="2026", academic_term="0",
        round_id="ROUND-A", session_proof="JSESSIONID=SECRET_TEST",
    )
    data.update(changes)
    return ReadbackScope(**data)


class ReadbackBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "readback-review.md"
        self.evidence.write_text("synthetic approved parser contract", encoding="utf-8")
        self.valid = AttestationRegistry(
            entries={}, root=self.root,
        ).with_entry(AttestationRegistry.issue(
            self.root, gate="official_enrollment_verified",
            evidence="readback-review.md", reviewer="offline-test",
        ))
        self.current = scope()
        self.calls = 0

    def bridge(self, reader, *, registry=None, state=None, source=PATH):
        return VerifiedS202ReadbackBridge(
            account_scope="fictional-owner", enrollment_semester=TERM,
            source_path=source,
            attestations=self.valid if registry is None else registry,
            current_scope=lambda: self.current if state is None else state(),
            read_snapshot=reader,
        )

    def snapshot(self, *, original=None, **updates):
        current = self.current if original is None else original
        fields = dict(
            account_scope=current.account_scope, semester=current.semester,
            school_origin=current.school_origin,
            internal_student_id=current.internal_student_id,
            academic_year=current.academic_year,
            academic_term=current.academic_term,
            round_id=current.round_id,
            identity_field="skbjdm", source_path=PATH,
            contract_sha256=sha256(self.evidence.read_bytes()).hexdigest(),
            observed_monotonic=time.monotonic(),
            records=frozenset({RECORD}),
        )
        fields.update(updates)
        return ExactSelectedSnapshot(**fields)

    def test_independent_attested_exact_read_returns_copied_set(self):
        async def reader(identity):
            self.calls += 1
            self.assertEqual(identity, self.current)
            return self.snapshot()
        bridge = self.bridge(reader)
        self.assertTrue(bridge.official_enrollment_verified)
        self.assertEqual(asyncio.run(bridge.read_enrollments()), {RECORD})
        self.assertEqual(self.calls, 1)
        self.assertNotIn("SECRET_TEST", repr(self.current))
        self.assertFalse(hasattr(bridge, "submit_once"))
        self.assertFalse(hasattr(bridge, "selection_window"))

    def test_missing_attestation_blocks_without_request(self):
        async def reader(_):
            self.calls += 1
            return self.snapshot()
        no_proof = AttestationRegistry(entries={}, root=self.root)
        bridge = self.bridge(reader, registry=no_proof)
        self.assertFalse(bridge.official_enrollment_verified)
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(bridge.read_enrollments())
        self.assertEqual(self.calls, 0)

    def test_display_only_page_and_unverified_records_cannot_confirm(self):
        for fake in (S202ReadbackResult("read"), set(), None):
            async def reader(_):
                return fake
            with self.subTest(fake=type(fake).__name__):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.bridge(reader).read_enrollments())

    def test_wrong_approved_source_or_internal_identity_is_rejected(self):
        for overrides in (
            {"source_path": "/hsjw/student/unreviewed.jsp"},
            {"identity_field": "skbjdm", "internal_student_id": "OTHER"},
            {"semester": "2026-2027-2"},
            {"round_id": "ROUND-B"},
        ):
            async def reader(_):
                return self.snapshot(**overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.bridge(reader).read_enrollments())

    def test_logged_out_or_changed_session_during_request_never_confirms(self):
        def state():
            return self.current
        async def reader(original):
            self.current = scope(session_proof="JSESSIONID=CHANGED")
            return self.snapshot(original=original)
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.bridge(reader, state=state).read_enrollments())

    def test_replaced_contract_file_during_request_fails_closed(self):
        async def reader(_):
            result = self.snapshot()
            self.evidence.write_text("changed contract", encoding="utf-8")
            return result
        bridge = self.bridge(reader)
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(bridge.read_enrollments())
        self.assertFalse(bridge.official_enrollment_verified)

    def test_old_or_future_snapshot_cannot_be_replayed(self):
        async def old_reader(_):
            return self.snapshot(observed_monotonic=time.monotonic() - 30)
        async def future_reader(_):
            return self.snapshot(observed_monotonic=time.monotonic() + 30)
        for reader in (old_reader, future_reader):
            with self.subTest(reader=reader.__name__):
                with self.assertRaises(EnrollmentNotReady):
                    asyncio.run(self.bridge(reader).read_enrollments())

    def test_reader_transport_failure_propagates_as_unknown(self):
        async def offline_failure(_):
            raise TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            asyncio.run(self.bridge(offline_failure).read_enrollments())

    def test_wrong_current_account_is_rejected_before_reader(self):
        self.current = scope(account_scope="OTHER-ACCOUNT")
        async def reader(_):
            self.calls += 1
            return self.snapshot()
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(self.bridge(reader).read_enrollments())
        self.assertEqual(self.calls, 0)

    def test_construction_rejects_unapproved_source_and_fake_registry(self):
        async def reader(_):
            return self.snapshot()
        with self.assertRaises(ValueError):
            self.bridge(reader, source="https://evil.invalid/fake")
        with self.assertRaises(ValueError):
            self.bridge(reader, source="/hsjw/fake?token=test")
        with self.assertRaises(ValueError):
            self.bridge(reader, registry=object())

    def test_verified_empty_snapshot_is_distinct_from_parse_failure(self):
        async def reader(_):
            return self.snapshot(records=frozenset())
        self.assertEqual(asyncio.run(self.bridge(reader).read_enrollments()), set())


if __name__ == "__main__":
    unittest.main()
