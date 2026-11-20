"""Proof-scoped form_provider boundary for a future approved S202 reader.

This module never guesses items/hidOption, queries university services, or
submits enrollment. It accepts only same-session, independently reviewed form
observations supplied by an explicitly injected first-party reader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import math
import time
from typing import Awaitable, Callable

from core.attestation import AttestationRegistry
from core.course_models import CourseTarget
from core.enrollment import EnrollmentNotReady
from core.ncwu_submission_transport import PreparedNCWUSubmission, SUBMIT_PATH
from core.ncwu_verified_readback_bridge import ReadbackScope

_WRITE_GATE = "official_write_verified"


@dataclass(frozen=True, slots=True, repr=False)
class CurrentS202FormObservation:
    """Transient form with independently reviewed item-to-internal-class proof."""
    scope: ReadbackScope
    source_path: str
    course_code: str
    internal_class_id: str
    selected_item: str
    fields: tuple[tuple[str, str], ...]
    binding_evidence: bytes = field(repr=False)
    observed_monotonic: float = 0.0

    def __repr__(self) -> str:
        return "CurrentS202FormObservation(<redacted>)"


class VerifiedS202FormProvider:
    """Callable for NCWUEvidenceBoundGateway; does no school HTTP on its own."""

    def __init__(
        self, *, account_scope: str, enrollment_semester: str,
        attestations: AttestationRegistry,
        current_scope: Callable[[], ReadbackScope],
        read_current_form: Callable[
            [CourseTarget, ReadbackScope], Awaitable[CurrentS202FormObservation]
        ],
        user_opted_in: bool = False,
        institution_permits_write: bool = False,
    ) -> None:
        if not isinstance(account_scope, str) or not account_scope.strip():
            raise ValueError("缺少已核验的本人账号范围")
        if not isinstance(enrollment_semester, str) or not enrollment_semester.strip():
            raise ValueError("缺少已核验的选课学期")
        if not isinstance(attestations, AttestationRegistry):
            raise ValueError("缺少可复核的正式提交协议验收登记")
        if not callable(current_scope) or not callable(read_current_form):
            raise ValueError("未提供当前 EXE 身份或已核实的第一方表单读取器")
        self.account_scope = account_scope
        self.enrollment_semester = enrollment_semester
        self.attestations = attestations
        self.current_scope = current_scope
        self.read_current_form = read_current_form
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write

    def _checked_scope(self) -> ReadbackScope:
        current = self.current_scope()
        if not isinstance(current, ReadbackScope):
            raise EnrollmentNotReady("正式提交缺少 EXE 本次已核实的会话身份")
        if (current.account_scope != self.account_scope
                or current.semester != self.enrollment_semester):
            raise EnrollmentNotReady("正式提交目标账号或学期与当前会话不一致")
        return current

    async def __call__(
        self, target: CourseTarget,
    ) -> tuple[PreparedNCWUSubmission, bytes]:
        if not isinstance(target, CourseTarget):
            raise EnrollmentNotReady("未指定唯一且有效的课程与内部教学班")
        if target.semester and target.semester != self.enrollment_semester:
            raise EnrollmentNotReady("志愿学期与当前官方会话不一致")
        if self.user_opted_in is not True or self.institution_permits_write is not True:
            raise EnrollmentNotReady("缺少本人明确授权或学校正式自动提交许可")
        # Check before and after awaiting a separately approved form reader.
        before_evidence = self.attestations.require(_WRITE_GATE)
        before = self._checked_scope()
        started = time.monotonic()
        observed = await self.read_current_form(target, before)
        completed = time.monotonic()
        after = self._checked_scope()
        if after != before:
            raise EnrollmentNotReady("取得表单期间 EXE 账号、轮次或会话已变化")
        after_evidence = self.attestations.require(_WRITE_GATE)
        if before_evidence.contract_sha256 != after_evidence.contract_sha256:
            raise EnrollmentNotReady("取得表单期间提交契约验收证据已替换")
        if not isinstance(observed, CurrentS202FormObservation):
            raise EnrollmentNotReady("未获得当前第一方表单的原始审查记录")
        if observed.scope != before:
            raise EnrollmentNotReady("提交表单与本次 EXE 账号、学期或轮次不一致")
        if observed.source_path != SUBMIT_PATH:
            raise EnrollmentNotReady("提交表单不属于已核实的 S202 正式提交路径")
        if (observed.course_code != target.course_id
                or observed.internal_class_id != target.class_id):
            raise EnrollmentNotReady("提交目标与官方内部教学班的原始映射不一致")
        stamp = observed.observed_monotonic
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not started <= stamp <= completed):
            raise EnrollmentNotReady("提交表单并非本次读取期间产生的实时证据")
        original = observed.binding_evidence
        if not isinstance(original, bytes) or not 1 <= len(original) <= 1024 * 1024:
            raise EnrollmentNotReady("没有经过复核的单教学班选课项原始证据")
        try:
            prepared = PreparedNCWUSubmission(
                fields=observed.fields,
                internal_student_id=before.internal_student_id,
                academic_year=before.academic_year,
                academic_term=before.academic_term,
                round_id=before.round_id,
                target_course_code=target.course_id,
                target_teaching_class_id=target.class_id,
                selected_item=observed.selected_item,
                item_binding_sha256=sha256(original).hexdigest(),
            )
        except ValueError as exc:
            raise EnrollmentNotReady("当前官网提交表单字段不完整或存在冲突") from exc
        # Gateway still requires durable intent and all four live attestations.
        return prepared, original
