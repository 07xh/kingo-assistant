"""Proof-scoped S202 readback bridge for a future approved native HTTP reader.

No school endpoint, HTTP client, enrollment POST, or credential persistence is
provided by this module. A separate, institution-approved implementation must
produce an ExactSelectedSnapshot from fresh official records. Display-only
course rows and result-page candidate fields are explicitly insufficient.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable
import time

from core.attestation import AttestationError, AttestationRegistry
from core.enrollment import EnrollmentNotReady
from core.ncwu_exact_readback import (
    ExactSelectedSnapshot, validate_exact_s202_readback,
)
from core.ncwu_live_elective import ORIGIN

_READ_GATE = "official_enrollment_verified"


@dataclass(frozen=True, slots=True)
class ReadbackScope:
    """Live EXE identity; the session token is never represented in logs."""

    account_scope: str
    semester: str
    school_origin: str
    internal_student_id: str
    academic_year: str
    academic_term: str
    round_id: str
    session_proof: str = field(repr=False)

    def __post_init__(self) -> None:
        values = (
            self.account_scope, self.semester, self.internal_student_id,
            self.academic_year, self.academic_term, self.round_id,
            self.session_proof,
        )
        if any(not isinstance(v, str) or not v.strip() or len(v) > 4096
               for v in values):
            raise ValueError("官方回读账号、学期、轮次或应用会话不完整")
        if self.school_origin != ORIGIN:
            raise ValueError("官方回读必须属于已核实的学校 HTTPS 来源")

    def as_identity(self) -> tuple[str, ...]:
        """Identity structure used by the strict existing snapshot validator."""
        return (
            self.account_scope, self.semester, self.school_origin,
            self.internal_student_id, self.academic_year,
            self.academic_term, self.round_id, self.session_proof,
        )


class VerifiedS202ReadbackBridge:
    """Adapter exposing read_enrollments() only through verified exact proof.

    Every read freshly validates the independent enrollment attestation.
    A future native reader must be explicitly injected; candidate HTML pages
    must NOT be injected as if they were authoritative selected records.
    The bridge can be used as a read-only recovery gateway, but it does not
    itself enable an EnrollmentEngine write adapter.
    """

    def __init__(
        self, *, account_scope: str, enrollment_semester: str,
        source_path: str, attestations: AttestationRegistry,
        current_scope: Callable[[], ReadbackScope],
        read_snapshot: Callable[[ReadbackScope], Awaitable[ExactSelectedSnapshot]],
    ) -> None:
        if (not isinstance(account_scope, str) or not account_scope.strip()
                or not isinstance(enrollment_semester, str)
                or not enrollment_semester.strip()):
            raise ValueError("官方回读缺少已验收的账号和学期范围")
        if (not isinstance(source_path, str)
                or not source_path.startswith("/hsjw/")
                or len(source_path) > 300
                or any(ch in source_path for ch in ("?", "#", "\\", "\r", "\n"))):
            raise ValueError("官方结果来源路径必须是经过核实的站内路径")
        if not isinstance(attestations, AttestationRegistry):
            raise ValueError("官方回读缺少真实的验收登记表")
        if not callable(current_scope) or not callable(read_snapshot):
            raise ValueError("官方回读缺少应用内会话或经过核实的结果读取器")
        self.account_scope = account_scope
        self.enrollment_semester = enrollment_semester
        self._source_path = source_path
        self._attestations = attestations
        self._current_scope = current_scope
        self._read_snapshot = read_snapshot

    @property
    def official_enrollment_verified(self) -> bool:
        """A document flag alone is not a usable readback capability."""
        try:
            self._attestations.require(_READ_GATE)
            return True
        except AttestationError:
            return False

    def _checked_scope(self) -> ReadbackScope:
        scope = self._current_scope()
        if not isinstance(scope, ReadbackScope):
            raise EnrollmentNotReady("未取得当前 EXE 本人登录的完整回读身份")
        if (scope.account_scope != self.account_scope
                or scope.semester != self.enrollment_semester):
            raise EnrollmentNotReady("当前账号或学期与官方回读适配器不一致")
        return scope

    async def read_enrollments(self) -> set[tuple[str, str]]:
        """One fresh, validated read. Never submits or retries a school write."""
        try:
            attestation = self._attestations.require(_READ_GATE)
        except AttestationError as exc:
            raise EnrollmentNotReady("官方已选记录的独立验收证据无效") from exc

        before = self._checked_scope()
        started = time.monotonic()
        snapshot = await self._read_snapshot(before)
        completed = time.monotonic()
        # Logout, re-login or a changed round during the await invalidates the
        # entire reply, even when a selected-class value appears to match.
        after = self._checked_scope()
        if after != before:
            raise EnrollmentNotReady("请求期间账号、学期、轮次或会话发生变化")
        if not isinstance(snapshot, ExactSelectedSnapshot):
            raise EnrollmentNotReady("展示页或候选结果不能充当官方精确已选记录")
        if snapshot.source_path != self._source_path:
            raise EnrollmentNotReady("结果不是已核验的官方已选记录接口")
        # A replacement/expired evidence file during the request cannot make
        # a previously checked record valid. Rehash on each verification.
        try:
            again = self._attestations.require(_READ_GATE)
        except AttestationError as exc:
            raise EnrollmentNotReady("读取期间官方结果协议验收状态失效") from exc
        if again.contract_sha256 != attestation.contract_sha256:
            raise EnrollmentNotReady("读取期间官方结果解析契约发生变化")

        return validate_exact_s202_readback(
            snapshot, identity=before.as_identity(),
            attestation=again, started=started, completed=completed,
        )
