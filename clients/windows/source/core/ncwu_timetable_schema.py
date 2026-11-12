"""Offline structural evidence for an NCWU personal timetable JSON response.

The observed endpoint supplies a *personal timetable*, not a verified elective
catalog, seat count, selection-window state, or enrollment capability. Never
return course records or values from potentially sensitive response payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

MAX_JSON_CHARS = 1024 * 1024
MAX_ENTRIES = 4096


class TimetableSchemaError(ValueError):
    """Unknown or malformed timetable shape; do not infer course availability."""


@dataclass(frozen=True)
class TimetableShape:
    """Counts only. This value MUST NOT authorize a live catalog adapter."""

    weekday_entries: int
    auxiliary_entries: int
    source_kind: str = "personal_timetable_only"
    elective_catalog_verified: bool = False
    seat_availability_verified: bool = False


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TimetableSchemaError("JSON 含有重复字段")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TimetableSchemaError("JSON 含有非标准数值")


def inspect_timetable_json(raw: str) -> TimetableShape:
    """Validate the *observed top-level schema* without parsing course content.

    Unknown inner record layouts are intentionally not assigned any semantic
    meaning. No network activity and no credentials are involved.
    """
    if not isinstance(raw, str) or not raw or len(raw) > MAX_JSON_CHARS:
        raise TimetableSchemaError("课表 JSON 为空或过大")
    try:
        obj = json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                         parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise TimetableSchemaError("课表不是无歧义的标准 JSON") from error
    if not isinstance(obj, dict):
        raise TimetableSchemaError("课表根节点不是对象")
    if any(not isinstance(obj.get(key), str) or len(obj[key]) > 64
           for key in ("xn", "xq", "zc")):
        raise TimetableSchemaError("学年、学期、周次字段结构不匹配")
    days = [obj.get(f"week{index}") for index in range(1, 8)]
    if any(not isinstance(day, list) for day in days):
        raise TimetableSchemaError("缺少七个有效课表日数组")
    supplementary = obj.get("sjhjinfo")
    if not isinstance(supplementary, list):
        raise TimetableSchemaError("辅助课表字段结构不匹配")
    total = sum(len(day) for day in days)
    if total > MAX_ENTRIES or len(supplementary) > MAX_ENTRIES:
        raise TimetableSchemaError("课表项目数异常")
    return TimetableShape(weekday_entries=total,
                          auxiliary_entries=len(supplementary))
