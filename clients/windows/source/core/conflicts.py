"""Conservative offline timetable overlap warnings for *manually entered* slots.

Syntax: `1:1-2;3:5-6` means Monday periods 1-2, Wednesday 5-6.
This deliberately does not infer campus-specific week numbers, special periods,
classes that alternate weeks, or the official enrollment conflict policy.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

PART = re.compile(r"^([1-7]):([1-9]|1[0-6])-([1-9]|1[0-6])$")


@dataclass(frozen=True)
class Slot:
    day: int
    first: int
    last: int


@dataclass(frozen=True)
class Conflict:
    first_key: str
    second_key: str
    day: int
    first_period: int
    last_period: int


def parse_slots(text: str) -> tuple[Slot, ...]:
    if not isinstance(text, str) or len(text) > 200:
        raise ValueError("节次长度无效")
    if not text.strip():
        return ()
    chunks = [chunk.strip() for chunk in text.split(";")]
    if len(chunks) > 14:
        raise ValueError("每门课程最多录入 14 段上课时间")
    result = []
    for chunk in chunks:
        match = PART.fullmatch(chunk)
        if not match:
            raise ValueError("节次格式应为 1:1-2;3:5-6（周:起-止节）")
        day, first, last = map(int, match.groups())
        if last < first:
            raise ValueError("结束节次不能早于开始节次")
        slot = Slot(day, first, last)
        if slot not in result:
            result.append(slot)
    return tuple(result)


def find_conflicts(courses: list[dict]) -> tuple[list[Conflict], list[str]]:
    """Return possible overlaps and keys with unknown/manual-missing schedules.

    Courses sharing one nonempty `group` are alternative choices; the overlap
    is intentionally skipped between those alternatives only.
    """
    entries = []
    missing = []
    for course in courses:
        key = str(course["key"])
        slots = parse_slots(course.get("time_slots", ""))
        if not slots:
            missing.append(key)
        entries.append((key, str(course.get("group", "")).strip(), slots))
    collisions = []
    for i, (key, group, slots) in enumerate(entries):
        for key2, group2, slots2 in entries[i+1:]:
            if group and group == group2:
                continue
            for a in slots:
                for b in slots2:
                    if a.day == b.day and a.first <= b.last and b.first <= a.last:
                        collisions.append(Conflict(key, key2, a.day, max(a.first, b.first), min(a.last, b.last)))
    return collisions, missing
