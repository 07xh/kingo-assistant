"""Shared synthetic attestation fixtures for offline tests.

Every registry built here is backed by a REAL temporary evidence file whose
sha256 is computed by the production code path, so the tests exercise digest
verification and expiry instead of bypassing them.
"""
from __future__ import annotations

import atexit
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.attestation import (
    GATE_NAMES,
    Attestation,
    AttestationRegistry,
    expiry_after,
)

_ROOT: Path | None = None


def evidence_root() -> Path:
    """One process-wide temp root holding synthetic acceptance documents."""
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp(prefix="kingo-attest-"))
        atexit.register(shutil.rmtree, _ROOT, True)
        for gate in GATE_NAMES:
            document = _ROOT / "docs" / f"{gate}.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(
                f"# Synthetic acceptance evidence\n\nGate: `{gate}`\n"
                "Test fixture only. No university request, credential or "
                "student record is represented by this file.\n",
                encoding="utf-8",
            )
    return _ROOT


def evidence_for(gate: str) -> str:
    evidence_root()
    return f"docs/{gate}.md"


def valid_registry(*, days: int = 30) -> AttestationRegistry:
    """A registry whose four gates are all valid and unexpired."""
    root = evidence_root()
    registry = AttestationRegistry(entries={}, root=root)
    for gate in GATE_NAMES:
        attestation = AttestationRegistry.issue(
            root, gate=gate, evidence=evidence_for(gate),
            reviewer="synthetic-test", expires_at=expiry_after(days),
            note="synthetic fixture",
        )
        registry = registry.with_entry(attestation)
    return registry


def registry_without(gate: str) -> AttestationRegistry:
    return valid_registry().without_entry(gate)


def expired_registry(gate: str) -> AttestationRegistry:
    root = evidence_root()
    now = datetime.now(UTC)
    attestation = AttestationRegistry.issue(
        root, gate=gate, evidence=evidence_for(gate), reviewer="synthetic-test",
        captured_at=now - timedelta(days=10),
        expires_at=now - timedelta(days=1),
        note="synthetic expired fixture",
    )
    return valid_registry().with_entry(attestation)


def tampered_registry(gate: str) -> AttestationRegistry:
    """Registry whose stored digest no longer matches the evidence file."""
    registry = valid_registry()
    original = registry.entries[gate]
    forged = Attestation(
        gate=original.gate, evidence=original.evidence,
        contract_sha256="0" * 64,
        captured_at=original.captured_at, expires_at=original.expires_at,
        reviewer=original.reviewer, note="forged digest",
    )
    return registry.with_entry(forged)
