"""Optional, identity-bound read-only timetable preparation before a selection window.

This never caches seat availability, enrollment records, or window permission.
A real gateway must explicitly attest that pre-window timetable reads are allowed
and provide a session-bound token. Default is no prefetch and a fresh read.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Sequence

from core.course_models import CourseTarget, ScheduleSnapshot
from core.enrollment import EnrollmentNotReady, _validate_targets

MAX_AGE_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class PreparedSchedule:
    captured_at: float
    token: tuple
    semester: str
    targets: tuple[tuple[str, str], ...]
    snapshot: ScheduleSnapshot

    def usable(self, gateway, targets: Sequence[CourseTarget]) -> bool:
        if (getattr(gateway, "preopen_schedule_permitted", False) is not True
                or getattr(gateway, "preopen_budget_headroom_verified", False) is not True):
            return False
        token_reader = getattr(gateway, "preflight_session_token", None)
        if not callable(token_reader):
            return False
        age = time.monotonic() - self.captured_at
        if not math.isfinite(age) or not 0 <= age <= MAX_AGE_SECONDS:
            return False
        if (self.semester != getattr(gateway, "enrollment_semester", None)
                or self.snapshot.semester != self.semester
                or self.snapshot.verified is not True):
            return False
        if self.targets != tuple((t.course_id, t.class_id) for t in targets):
            return False
        try:
            return token_reader() == self.token
        except Exception:
            return False


async def prepare_schedule(engine, targets: Sequence[CourseTarget]) -> PreparedSchedule | None:
    """One optional permitted *read* only. Unknown permissions imply no prefetch."""
    targets = tuple(targets)
    _validate_targets(targets, engine.journal.semester)
    engine.validate_ready()
    gateway = engine.gateway
    # A pre-window read can consume the rate budget needed at T0. Unless
    # capacity for both the optional read and mandatory post-window checks
    # has been independently verified, skip prefetch entirely.
    if (getattr(gateway, "preopen_schedule_permitted", False) is not True
            or getattr(gateway, "preopen_budget_headroom_verified", False) is not True):
        return None
    token_reader = getattr(gateway, "preflight_session_token", None)
    if not callable(token_reader):
        return None
    before = token_reader()
    if not isinstance(before, tuple) or not before:
        raise EnrollmentNotReady("无法绑定提前预检的真实会话")
    snapshot = await gateway.read_schedule()
    after = token_reader()
    if before != after:
        raise EnrollmentNotReady("预检期间账号或会话变化")
    if (not isinstance(snapshot, ScheduleSnapshot)
            or snapshot.verified is not True
            or snapshot.semester != engine.journal.semester):
        raise EnrollmentNotReady("提前读取的课表未通过官方学期核验")
    return PreparedSchedule(
        time.monotonic(), after, engine.journal.semester,
        tuple((t.course_id, t.class_id) for t in targets), snapshot,
    )
