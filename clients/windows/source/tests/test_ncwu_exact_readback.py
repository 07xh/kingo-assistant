"""Offline exact selected-class proof tests; no school HTTP."""
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import time
import unittest
from types import SimpleNamespace

from core.enrollment import EnrollmentNotReady
from core.ncwu_exact_readback import (
    ExactSelectedSnapshot, validate_exact_s202_readback,
)

DIGEST = sha256(b"fictional verified parser contract for unit testing").hexdigest()
IDENTITY = (
    "account-test", "2026-0", "https://jwmis.ncwu.edu.cn",
    "OWNER", "2026", "0", "ROUND", "JSESSIONID=synthetic",
)


def snapshot(*, records=(), **changes):
    values = dict(
        account_scope=IDENTITY[0], semester=IDENTITY[1],
        school_origin=IDENTITY[2], internal_student_id=IDENTITY[3],
        academic_year=IDENTITY[4], academic_term=IDENTITY[5],
        round_id=IDENTITY[6], identity_field="skbjdm",
        source_path="/hsjw/student/verified-exact-test.jsp",
        contract_sha256=DIGEST, observed_monotonic=time.monotonic(),
        records=frozenset(records),
    )
    values.update(changes)
    return ExactSelectedSnapshot(**values)


class ExactProofTests(unittest.TestCase):
    def check(self, value, *, digest=DIGEST, begin=None, end=None):
        timestamp = value.observed_monotonic
        return validate_exact_s202_readback(
            value, identity=IDENTITY,
            attestation=SimpleNamespace(contract_sha256=digest),
            started=timestamp - 0.01 if begin is None else begin,
            completed=timestamp + 0.01 if end is None else end,
        )

    def test_exact_current_internal_class_is_readable_without_write(self):
        source = snapshot(records={("C101", "SKBJDM-INTERNAL")})
        records = self.check(source)
        self.assertEqual(records, {("C101", "SKBJDM-INTERNAL")})
        self.assertIsNot(records, source.records)
        with self.assertRaises(FrozenInstanceError):
            source.round_id = "new"

    def test_empty_confirmed_snapshot_is_valid_but_plain_empty_set_is_not(self):
        self.assertEqual(self.check(snapshot()), set())
        with self.assertRaises(EnrollmentNotReady):
            validate_exact_s202_readback(
                set(), identity=IDENTITY,
                attestation=SimpleNamespace(contract_sha256=DIGEST),
                started=time.monotonic() - 1,
                completed=time.monotonic() + 1,
            )

    def test_visible_class_id_is_not_an_accepted_identity_field(self):
        with self.assertRaises(ValueError):
            snapshot(identity_field="skbh")
        with self.assertRaises(ValueError):
            snapshot(identity_field="display_class")
        with self.assertRaises(ValueError):
            snapshot(source_path="https://other.example/class")

    def test_wrong_student_term_or_round_never_confirms(self):
        for key, value in (
            ("account_scope", "other-account"),
            ("semester", "2026-1"),
            ("internal_student_id", "OTHER"),
            ("round_id", "NEW"),
            ("academic_term", "1"),
        ):
            with self.subTest(key=key), self.assertRaises(EnrollmentNotReady):
                self.check(snapshot(**{key: value}))

    def test_other_contract_digest_or_unbound_attestation_is_rejected(self):
        value = snapshot(records={("C101", "INTERNAL")})
        for digest in (None, "0"*64, ""):
            with self.subTest(digest=digest), self.assertRaises(EnrollmentNotReady):
                self.check(value, digest=digest)

    def test_old_cache_and_future_timestamp_not_trusted(self):
        value = snapshot()
        with self.assertRaises(EnrollmentNotReady):
            self.check(value, begin=value.observed_monotonic + 1)
        with self.assertRaises(EnrollmentNotReady):
            self.check(value, end=value.observed_monotonic - 1)

    def test_invalid_record_formats_rejected_before_readout(self):
        for records in (
            [("C101", "CLASS")], {("C101", "CLASS")},
            frozenset({("C101", "BAD", "EXTRA")}),
            frozenset({("C101", "")}),
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                # Pass the *raw* malformed collection into dataclass validation;
                # snapshot() intentionally converts ordinary test inputs to a
                # frozenset to model a legitimate source.
                replace(snapshot(), records=records)
        with self.assertRaises(ValueError):
            snapshot(records=frozenset({("C101", "X"*110)}))

    def test_fake_attestation_or_source_path_cannot_produce_success(self):
        source = snapshot(records={("C101", "INTERNAL")})
        with self.assertRaises(EnrollmentNotReady):
            validate_exact_s202_readback(
                source, identity=IDENTITY, attestation=object(),
                started=source.observed_monotonic,
                completed=source.observed_monotonic,
            )
        for path in ("/other/path", "/hsjw/foo?token=SECRET",
                     "/hsjw/foo#fragment", "/hsjw\\foo"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                snapshot(source_path=path)
