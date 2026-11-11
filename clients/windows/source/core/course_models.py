"""Immutable offline course, teaching-class and timetable contracts.

No network access or university-specific endpoint assumptions live here.
A missing or unverified timetable is UNKNOWN, never an assertion of safety.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class TimeSlot:
    """A weekday/period span on explicitly known teaching weeks."""
    day: int
    first: int
    last: int
    weeks: frozenset[int] | None = None

    def __post_init__(self) -> None:
        if (type(self.day) is not int or not 1 <= self.day <= 7
                or type(self.first) is not int or type(self.last) is not int
                or not 1 <= self.first <= self.last <= 16):
            raise ValueError("上课时间的星期或节次无效")
        if self.weeks is not None:
            if (not isinstance(self.weeks, frozenset) or not self.weeks
                    or any(type(w) is not int or not 1 <= w <= 60 for w in self.weeks)):
                raise ValueError("教学周必须是有效且非空的不可变集合")


@dataclass(frozen=True, slots=True)
class CourseTarget:
    key: str
    course_id: str
    class_id: str
    group: str = ""
    semester: str = ""
    course_name: str = ""
    teacher: str = ""
    time_slots: tuple[TimeSlot, ...] | None = None

    def __post_init__(self) -> None:
        for value in (self.key, self.course_id, self.class_id):
            if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 100
                    or any(ord(c) < 32 for c in value)):
                raise ValueError("必须提供明确有效的课程代码和教学班编号")
        for value in (self.group, self.semester, self.course_name, self.teacher):
            if (not isinstance(value, str) or len(value) > 150
                    or any(ord(c) < 32 for c in value)):
                raise ValueError("课程附加信息无效")
        _validate_slots(self.time_slots)


@dataclass(frozen=True, slots=True)
class CourseOffering:
    course_id: str
    class_id: str
    remain: int | None
    semester: str = ""
    course_name: str = ""
    teacher: str = ""
    time_slots: tuple[TimeSlot, ...] | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.course_id, str) or not self.course_id.strip()
                or not isinstance(self.class_id, str) or not self.class_id.strip()):
            raise ValueError("教学班身份缺失")
        if self.remain is not None and (type(self.remain) is not int or self.remain < 0):
            raise ValueError("余量不可信")
        for value in (self.semester, self.course_name, self.teacher):
            if not isinstance(value, str) or len(value) > 150:
                raise ValueError("教学班属性无效")
        _validate_slots(self.time_slots)


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    """A checked snapshot. Empty slots means confirmed no occupied periods."""
    semester: str
    slots: tuple[TimeSlot, ...]
    verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.semester, str) or not self.semester.strip():
            raise ValueError("课表学期缺失")
        if type(self.verified) is not bool:
            raise ValueError("课表核验状态无效")
        _validate_slots(self.slots)


def _validate_slots(value: tuple[TimeSlot, ...] | None) -> None:
    if value is not None and (not isinstance(value, tuple)
                              or any(not isinstance(slot, TimeSlot) for slot in value)):
        raise ValueError("时间槽必须是不可变的 TimeSlot 元组")


class ConflictVerdict(str, Enum):
    CLEAR = "clear"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


def time_conflict(candidate: tuple[TimeSlot, ...] | None,
                  occupied: tuple[TimeSlot, ...] | None) -> ConflictVerdict:
    """Conservative week-aware overlap; unknown weeks never mean no conflict."""
    if candidate is None or occupied is None:
        return ConflictVerdict.UNKNOWN
    _validate_slots(candidate)
    _validate_slots(occupied)
    uncertain = False
    for a in candidate:
        for b in occupied:
            if a.day != b.day or a.last < b.first or b.last < a.first:
                continue
            if a.weeks is None or b.weeks is None:
                uncertain = True
            elif not a.weeks.isdisjoint(b.weeks):
                return ConflictVerdict.CONFLICT
    return ConflictVerdict.UNKNOWN if uncertain else ConflictVerdict.CLEAR


def offering_matches(target: CourseTarget, offering: CourseOffering,
                     semester: str) -> bool:
    """Do not accept a row without matching semester and exact teaching class."""
    if (target.semester and target.semester != semester) or offering.semester != semester:
        return False
    if target.course_id != offering.course_id or target.class_id != offering.class_id:
        return False
    for expected, actual in ((target.course_name, offering.course_name),
                             (target.teacher, offering.teacher)):
        if expected and expected.strip().casefold() != actual.strip().casefold():
            return False
    if target.time_slots is not None and target.time_slots != offering.time_slots:
        return False
    return True
