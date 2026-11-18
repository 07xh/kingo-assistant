"""Evidence-bound bridge from the existing enrollment engine to NCWU S202.

It connects the existing *real* scheduler/EnrollmentEngine to the single-shot
HTTP POST transport. It never creates a fictitious availability, course-class
identity or write form. A source with genuine selected-course records, real
seat counts, verified timetable and current first-party form is REQUIRED.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Mapping

from core.attestation import AttestationRegistry, GATE_NAMES
from core.course_models import CourseTarget
from core.enrollment import (
    AttemptJournal, EnrollmentEngine, EnrollmentNotReady, SubmitReceipt,
)
from core.ncwu_live_elective import ORIGIN, _validate_origin
from core.ncwu_submission_transport import (
    PreparedNCWUSubmission, submit_prepared_s202_once,
)
from core.ncwu_read_support import default_read_client


class NCWUEvidenceBoundGateway:
    """A VerifiedGateway-compatible composition, not a self-arming shortcut."""

    _READ_METHODS = (
        "selection_window", "read_enrollments", "list_offerings", "read_schedule",
    )
    _CAPABILITY_NAMES = GATE_NAMES

    def __init__(
        self, *,
        source,
        journal: AttemptJournal,
        form_provider: Callable[[CourseTarget], Awaitable[tuple[PreparedNCWUSubmission, bytes]]],
        attestations: AttestationRegistry,
        config: Mapping,
        request_budget,
        user_opted_in: bool,
        institution_permits_write: bool,
        http_factory=default_read_client,
    ) -> None:
        if not isinstance(journal, AttemptJournal):
            raise TypeError("需要已有的持久化提交记录")
        if not callable(form_provider):
            raise TypeError("缺少当前官网会话生成的提交表单")
        if any(not callable(getattr(source, name, None)) for name in self._READ_METHODS):
            raise EnrollmentNotReady("缺少真实官网课程目录、课表、窗口或已选记录读取器")
        self.source = source
        self.journal = journal
        self.form_provider = form_provider
        self.attestations = attestations
        self.config = config
        self.request_budget = request_budget
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write
        self.http_factory = http_factory

    @property
    def account_scope(self):
        return getattr(self.source, "account_scope", None)

    @property
    def enrollment_semester(self):
        return getattr(self.source, "enrollment_semester", None)

    def __getattr__(self, name: str):
        if name in self._CAPABILITY_NAMES:
            return getattr(self.source, name, None) is True
        raise AttributeError(name)

    def _identity_snapshot(self) -> tuple:
        source = self.source
        return (
            getattr(source, "account_scope", None),
            getattr(source, "enrollment_semester", None),
            getattr(source, "verified_school_origin", None),
            getattr(source, "current_internal_student_id", None),
            getattr(source, "current_academic_year", None),
            getattr(source, "current_academic_term", None),
            getattr(source, "current_round_id", None),
            getattr(source, "session_cookie", None),
        )

    def _read_identity_ok(self) -> bool:
        state = self._identity_snapshot()
        return (
            state[0] == self.journal.account_scope
            and state[1] == self.journal.semester
            and state[2] == ORIGIN
            and all(isinstance(s, str) and bool(s) for s in state)
            and _validate_origin(self.config, state[7])
        )

    def _assert_ready(self) -> None:
        if not self._read_identity_ok():
            raise EnrollmentNotReady("EXE 当前官方账号、学期或会话与提交记录不一致")
        if self.user_opted_in is not True or self.institution_permits_write is not True:
            raise EnrollmentNotReady("未经本人授权或学校许可，不执行自动提交")
        for name in self._CAPABILITY_NAMES:
            if getattr(self, name) is not True:
                raise EnrollmentNotReady(f"{name} 未由官网数据源验收")
            if self.attestations is None:
                raise EnrollmentNotReady("四项接口验收证据缺失")
            self.attestations.require(name)

    async def selection_window(self):
        return await self.source.selection_window()

    async def list_offerings(self):
        return await self.source.list_offerings()

    async def read_schedule(self):
        return await self.source.read_schedule()

    async def read_enrollments(self):
        if not self._read_identity_ok() or not self.official_enrollment_verified:
            raise EnrollmentNotReady("已选结果页和本人账号尚未关联")
        records = await self.source.read_enrollments()
        if not self._read_identity_ok():
            raise EnrollmentNotReady("结果回读期间官方账号已切换")
        return records

    async def submit_once(self, target: CourseTarget) -> SubmitReceipt:
        self._assert_ready()
        if not isinstance(target, CourseTarget) or self.journal.status(target) != "pending":
            raise EnrollmentNotReady("没有已经持久化的一次性提交意图")
        if target.semester and target.semester != self.enrollment_semester:
            raise EnrollmentNotReady("目标学期与官网轮次不一致")
        before = self._identity_snapshot()
        # This provider is NOT the S202 display-table parser. It must be a
        # separately proven source for the actual official "items" packet.
        prepared, original_evidence = await self.form_provider(target)
        if self._identity_snapshot() != before:
            raise EnrollmentNotReady("获取提交表单期间官方会话已变化")
        return await submit_prepared_s202_once(
            self.config,
            session_cookie=before[7],
            prepared=prepared,
            expected_course_code=target.course_id,
            expected_internal_class_id=target.class_id,
            current_internal_student_id=before[3],
            current_academic_year=before[4],
            current_academic_term=before[5],
            current_round_id=before[6],
            item_binding_evidence=original_evidence,
            attestations=self.attestations,
            user_opted_in=self.user_opted_in,
            institution_permits_write=self.institution_permits_write,
            request_budget=self.request_budget,
            http_factory=self.http_factory,
        )

    async def aclose(self):
        closer = getattr(self.source, "aclose", None)
        if callable(closer):
            await closer()


def make_ncwu_enrollment_engine(*, source, journal, form_provider,
                                attestations, config, request_budget,
                                user_opted_in=False,
                                institution_permits_write=False,
                                http_factory=default_read_client) -> EnrollmentEngine:
    """Inject into DesktopScheduledWorker only after the four gates are proven.

    No GUI auto-registers this builder. Live form and exact per-class selected
    result adapters are still missing; do not represent it as enabled.
    """
    gateway = NCWUEvidenceBoundGateway(
        source=source, journal=journal,
        form_provider=form_provider, attestations=attestations,
        config=config, request_budget=request_budget,
        user_opted_in=user_opted_in,
        institution_permits_write=institution_permits_write,
        http_factory=http_factory,
    )
    return EnrollmentEngine(
        gateway, journal,
        user_opted_in=user_opted_in,
        institution_permits_write=institution_permits_write,
        attestations=attestations,
    )
