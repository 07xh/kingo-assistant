"""Offline tests for evidence-driven attestation and the write gate.

Every fixture writes a real temporary document, so digest verification,
tamper detection and expiry are exercised instead of bypassed.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.attestation import (
    GATE_NAMES, Attestation, AttestationError, AttestationRegistry,
    expiry_after, hash_evidence, resolve_registry,
)
from core.enrollment import EnrollmentEngine, EnrollmentNotReady
from tests.attestation_support import (
    evidence_for, evidence_root, expired_registry, registry_without,
    tampered_registry, valid_registry,
)
from tests.test_enrollment import FakeOfficialGateway


TERM = "2026-2027-1"


def journal_for(directory: Path):
    from core.enrollment import AttemptJournal
    return AttemptJournal(Path(directory) / "journal.json", TERM,
                          account_scope="synthetic-account-A")


class AttestationModelTests(unittest.TestCase):
    def test_all_four_gates_are_covered_by_a_valid_registry(self):
        registry = valid_registry()
        self.assertEqual(set(registry.entries), set(GATE_NAMES))
        self.assertEqual(registry.missing(), ())
        for gate in GATE_NAMES:
            self.assertEqual(registry.verify(gate).gate, gate)
        self.assertTrue(registry.all_valid())

    def test_unknown_gate_is_rejected_at_construction(self):
        with self.assertRaises(AttestationError):
            Attestation(gate="made_up_gate", evidence=evidence_for(GATE_NAMES[0]),
                        contract_sha256="a" * 64,
                        captured_at=datetime.now(timezone.utc),
                        expires_at=None, reviewer="t")

    def test_naive_datetime_and_bad_digest_are_rejected(self):
        with self.assertRaises(AttestationError):
            Attestation(gate=GATE_NAMES[0], evidence="x.md", contract_sha256="a" * 64,
                        captured_at=datetime.now(), expires_at=None, reviewer="t")
        with self.assertRaises(AttestationError):
            Attestation(gate=GATE_NAMES[0], evidence="x.md", contract_sha256="zz",
                        captured_at=datetime.now(timezone.utc), expires_at=None,
                        reviewer="t")

    def test_expiry_must_be_after_capture(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(AttestationError):
            Attestation(gate=GATE_NAMES[0], evidence="x.md", contract_sha256="a" * 64,
                        captured_at=now, expires_at=now - timedelta(days=1),
                        reviewer="t")

    def test_issue_requires_an_existing_evidence_document(self):
        with self.assertRaises(AttestationError):
            AttestationRegistry.issue(evidence_root(), gate=GATE_NAMES[0],
                                      evidence="docs/does-not-exist.md",
                                      reviewer="t")

    def test_evidence_may_not_escape_the_root(self):
        with self.assertRaises(AttestationError):
            AttestationRegistry.issue(evidence_root(), gate=GATE_NAMES[0],
                                      evidence="../../etc/passwd", reviewer="t")
        with self.assertRaises(AttestationError):
            AttestationRegistry.issue(evidence_root(), gate=GATE_NAMES[0],
                                      evidence=str(Path("/etc/passwd")), reviewer="t")


class AttestationVerificationTests(unittest.TestCase):
    def test_expired_attestation_fails(self):
        registry = expired_registry(GATE_NAMES[3])
        with self.assertRaises(AttestationError) as caught:
            registry.verify(GATE_NAMES[3])
        self.assertIn("过期", str(caught.exception))
        self.assertFalse(registry.all_valid())

    def test_tampered_evidence_is_detected(self):
        registry = tampered_registry(GATE_NAMES[0])
        with self.assertRaises(AttestationError) as caught:
            registry.verify(GATE_NAMES[0])
        self.assertIn("已被修改", str(caught.exception))

    def test_rehash_can_be_skipped_without_accepting_expiry(self):
        registry = tampered_registry(GATE_NAMES[0])
        self.assertEqual(registry.verify(GATE_NAMES[0], rehash=False).gate,
                         GATE_NAMES[0])

    def test_missing_gate_reports_a_specific_message(self):
        registry = registry_without(GATE_NAMES[2])
        self.assertEqual(registry.missing(), (GATE_NAMES[2],))
        with self.assertRaises(AttestationError):
            registry.verify(GATE_NAMES[2])

    def test_edited_evidence_unlocks_after_reissue(self):
        root = evidence_root()
        registry = valid_registry()
        document = root / registry.entries[GATE_NAMES[1]].evidence
        original = document.read_bytes()
        try:
            document.write_bytes(original + b"\nedited\n")
            with self.assertRaises(AttestationError):
                registry.verify(GATE_NAMES[1])
            reissued = registry.with_entry(AttestationRegistry.issue(
                root, gate=GATE_NAMES[1], evidence=evidence_for(GATE_NAMES[1]),
                reviewer="t", expires_at=expiry_after(30)))
            self.assertEqual(reissued.verify(GATE_NAMES[1]).gate, GATE_NAMES[1])
        finally:
            document.write_bytes(original)

    def test_hash_evidence_matches_hashlib(self):
        import hashlib
        document = evidence_root() / evidence_for(GATE_NAMES[0])
        self.assertEqual(hash_evidence(document),
                         hashlib.sha256(document.read_bytes()).hexdigest())


class RegistryPersistenceTests(unittest.TestCase):
    def test_round_trip_through_disk_preserves_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            valid_registry().save(path)
            loaded = AttestationRegistry.load(path, evidence_root())
            self.assertTrue(loaded.all_valid())

    def test_missing_registry_file_yields_an_empty_fail_closed_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = AttestationRegistry.load(Path(directory) / "absent.json")
            self.assertEqual(registry.entries, {})
            self.assertFalse(registry.all_valid())

    def test_corrupt_json_is_an_error_not_an_empty_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(AttestationError):
                AttestationRegistry.load(path)

    def test_wrong_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            path.write_text(json.dumps({"version": 99, "attestations": []}),
                            encoding="utf-8")
            with self.assertRaises(AttestationError):
                AttestationRegistry.load(path)

    def test_duplicate_gate_entries_are_rejected(self):
        document = valid_registry().to_document()
        document["attestations"].append(dict(document["attestations"][0]))
        with self.assertRaises(AttestationError):
            AttestationRegistry.from_document(document, evidence_root())

    def test_describe_is_privacy_safe_and_counts_valid_gates(self):
        report = valid_registry().describe()
        self.assertEqual(report["total"], 4)
        self.assertEqual(report["valid"], 4)
        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("password", serialized.lower())
        self.assertNotIn("cookie", serialized.lower())

    def test_resolve_registry_accepts_none_string_and_mapping(self):
        self.assertEqual(resolve_registry(None).entries, {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attestations.json"
            valid_registry().save(path)
            self.assertTrue(resolve_registry(str(path), repo_root=evidence_root()).all_valid())
            self.assertTrue(resolve_registry({"path": str(path)},
                                            repo_root=evidence_root()).all_valid())
        with self.assertRaises(AttestationError):
            resolve_registry(42)


class WriteGateTests(unittest.TestCase):
    """The engine must refuse every unattested capability before any write."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.journal = journal_for(self.directory)

    def gateway(self):
        return FakeOfficialGateway([])

    def engine(self, **kwargs):
        return EnrollmentEngine(
            self.gateway(), self.journal,
            user_opted_in=True, institution_permits_write=True, **kwargs)

    def test_engine_without_registry_cannot_run(self):
        with self.assertRaises(EnrollmentNotReady) as caught:
            self.engine().validate_ready()
        self.assertIn("证据登记表", str(caught.exception))

    def test_empty_registry_blocks_the_write_gate(self):
        with self.assertRaises(EnrollmentNotReady) as caught:
            self.engine(attestations=AttestationRegistry(entries={},
                                                        root=evidence_root())
                        ).validate_ready()
        self.assertIn("尚未登记", str(caught.exception))

    def test_any_single_missing_gate_blocks_the_write_gate(self):
        for gate in GATE_NAMES:
            with self.subTest(gate=gate):
                engine = self.engine(attestations=registry_without(gate))
                with self.assertRaises(EnrollmentNotReady) as caught:
                    engine.validate_ready()
                self.assertTrue(gate in str(caught.exception)
                                or "尚未登记" in str(caught.exception))

    def test_expired_gate_blocks_the_write_gate(self):
        with self.assertRaises(EnrollmentNotReady) as caught:
            self.engine(attestations=expired_registry(GATE_NAMES[3])).validate_ready()
        self.assertIn("过期", str(caught.exception))

    def test_tampered_gate_blocks_the_write_gate(self):
        with self.assertRaises(EnrollmentNotReady) as caught:
            self.engine(attestations=tampered_registry(GATE_NAMES[0])).validate_ready()
        self.assertIn("已被修改", str(caught.exception))

    def test_full_evidence_lets_the_gate_pass_without_network(self):
        engine = self.engine(attestations=valid_registry())
        engine.validate_ready()  # must not raise

    def test_gateway_flag_false_still_blocks_even_with_valid_evidence(self):
        gateway = self.gateway()
        gateway.official_write_verified = False
        engine = EnrollmentEngine(
            gateway, self.journal, user_opted_in=True,
            institution_permits_write=True, attestations=valid_registry())
        with self.assertRaises(EnrollmentNotReady) as caught:
            engine.validate_ready()
        self.assertIn("official_write_verified", str(caught.exception))

    def test_user_opt_in_is_still_required(self):
        engine = EnrollmentEngine(
            self.gateway(), self.journal, user_opted_in=False,
            institution_permits_write=True, attestations=valid_registry())
        with self.assertRaises(EnrollmentNotReady):
            engine.validate_ready()

    def test_institution_permission_is_still_required(self):
        engine = EnrollmentEngine(
            self.gateway(), self.journal, user_opted_in=True,
            institution_permits_write=False, attestations=valid_registry())
        with self.assertRaises(EnrollmentNotReady):
            engine.validate_ready()


if __name__ == "__main__":
    unittest.main()
