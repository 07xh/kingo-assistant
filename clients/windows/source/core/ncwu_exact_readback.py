"""Fail-closed official S202 selected-class readback contract.

The HTML display-only page and its course/class-number candidates can never
serve as authoritative enrollment records. A future first-party source MUST
return a fresh, proof-scoped snapshot containing exact internal teaching-class
identities. Merely finding a course name or receiving HTTP 200 is insufficient.

No HTTP requests, credentials, or school-derived field values live here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re

from core.enrollment import EnrollmentNotReady, _read_records
from core.ncwu_live_elective import ORIGIN

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ExactSelectedSnapshot:
    """One independent official read for the current account and round.

    The producer must read actual official course code + **internal** skbjdm,
    not skbh, in the same account and session used by the enrollment engine.
    It must timestamp fresh response parsing with time.monotonic() and bind
    the approved parsing contract to its hash-verified attestation digest.
    """

    account_scope: str
    semester: str
    school_origin: str
    internal_student_id: str
    academic_year: str
    academic_term: str
    round_id: str
    identity_field: str
    source_path: str
    contract_sha256: str
    observed_monotonic: float
    records: frozenset[tuple[str, str]]

    def __post_init__(self) -> None:
        for field in (
            self.account_scope, self.semester, self.school_origin,
            self.internal_student_id, self.academic_year,
            self.academic_term, self.round_id,
        ):
            if not isinstance(field, str) or not field or len(field) > 256:
                raise ValueError("官方结果快照的本人账号、学期或轮次字段异常")
        if self.school_origin != ORIGIN:
            raise ValueError("官方结果快照不是已知的华水来源")
        if self.identity_field != "skbjdm":
            raise ValueError("只有官网内部教学班标识 skbjdm 才能确认选课")
        if (not isinstance(self.source_path, str)
                or not self.source_path.startswith("/hsjw/")
                or len(self.source_path) > 300
                or any(c in self.source_path for c in ("?", "#", "\\", "\r", "\n"))):
            raise ValueError("官方结果快照缺少原站结果接口路径")
        if not isinstance(self.contract_sha256, str) or not _SHA256.fullmatch(
            self.contract_sha256
        ):
            raise ValueError("正式结果解析器的验收证据摘要无效")
        if (type(self.observed_monotonic) not in (int, float)
                or not math.isfinite(self.observed_monotonic)
                or self.observed_monotonic < 0):
            raise ValueError("结果快照缺少本次实际读取时间")
        if not isinstance(self.records, frozenset) or len(self.records) > 200:
            raise ValueError("官方已选记录必须为有界且不可变的精确标识集合")
        validated = _read_records(self.records)
        if any(len(course) > 100 or len(klass) > 100 for course, klass in validated):
            raise ValueError("官方结果包含异常长度的课程或教学班标识")


def validate_exact_s202_readback(
    snapshot: ExactSelectedSnapshot, *,
    identity: tuple, attestation, started: float, completed: float,
) -> set[tuple[str, str]]:
    """Validate read scope, evidence identity and freshness before engine use.

    An empty *verified* set may mean the owner has no enrolled records;
    missing/unverified data must raise instead of masquerading as empty.
    """
    if not isinstance(snapshot, ExactSelectedSnapshot):
        raise EnrollmentNotReady("官方结果未提供独立核验的内部教学班记录")
    if not isinstance(identity, tuple) or len(identity) != 8:
        raise EnrollmentNotReady("官方账号、学期或会话身份不完整")
    expected = (
        identity[0], identity[1], identity[2], identity[3],
        identity[4], identity[5], identity[6],
    )
    observed = (
        snapshot.account_scope, snapshot.semester, snapshot.school_origin,
        snapshot.internal_student_id, snapshot.academic_year,
        snapshot.academic_term, snapshot.round_id,
    )
    if observed != expected:
        raise EnrollmentNotReady("官方结果的账号、学期或轮次与当前会话不一致")
    contract_sha = getattr(attestation, "contract_sha256", None)
    if (not isinstance(contract_sha, str)
            or snapshot.contract_sha256 != contract_sha):
        raise EnrollmentNotReady("结果解析契约未与当前验收证据绑定")
    if (type(started) not in (int, float) or type(completed) not in (int, float)
            or not math.isfinite(started) or not math.isfinite(completed)
            or not started <= snapshot.observed_monotonic <= completed):
        raise EnrollmentNotReady("官方结果不是本次请求期间产生的新鲜快照")
    # The engine accepts a plain set only AFTER this separate proof boundary.
    return set(snapshot.records)
