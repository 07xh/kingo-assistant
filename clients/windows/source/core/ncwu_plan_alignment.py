"""Offline comparison of native NCWU course-plan rows to local target courses.

This is *preparation*, not eligibility or a successful enrollment. Matching
the plan's course code and teaching-class identifier must not promote a
historical plan seat count to live S202 availability, and the school's visible
teaching-class number may differ from the underlying class identifier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from core.ncwu_course_plan import CoursePlanRow, PlanSnapshot

_TERM = re.compile(r"^([0-9]{4})-([0-9]{4})-([12])$")
_PLACEHOLDER = ("FILL_FROM_", "SECOND_OPTION_ID")
_LABELS = {
    "matched_ids": "课程和教学班标识均在计划中",
    "metadata_conflict": "课程名称或教师与计划不一致",
    "metadata_unverified": "课程和教学班标识匹配，但部分资料未核实",
    "ambiguous": "计划中存在重复或不一致的同编号教学班",
    "course_id_missing": "目标课程代码未填写或仍是模板",
    "course_not_in_plan": "计划快照中未找到该课程代码",
    "class_id_missing": "找到课程，但目标教学班编号未填写",
    "class_unknown": "找到课程，但学校快照未给出可比较的教学班标识",
    "class_unmatched": "找到课程，但未找到完全相同的教学班标识",
    "semester_unverified": "本地或学校学期尚不能可靠对照",
    "semester_mismatch": "学校快照学期与本地目标学期不一致",
}


@dataclass(frozen=True)
class TargetAlignment:
    key: str
    course_id: str
    class_id: str
    status: str
    candidates: int = 0

    @property
    def description(self) -> str:
        return _LABELS[self.status]


@dataclass(frozen=True)
class PlanAlignmentReport:
    semester_status: str
    targets: tuple[TargetAlignment, ...]

    @property
    def confirmed_ids(self) -> int:
        return sum(item.status == "matched_ids" for item in self.targets)

    @property
    def message(self) -> str:
        if not self.targets:
            return (
                "未配置目标志愿；可以在「编辑课程与监测设置」中填写。"
                "此处只对照开课计划，不会创建或提交选课请求。"
            )
        prefix = {
            "aligned": "学校开课计划学期与本地目标学期一致",
            "mismatch": "学校计划与本地目标学期不同；停止逐项判断",
            "unverified": "本地或学校学期字段不完整；暂不判断教学班",
        }[self.semester_status]
        head = (
            f"{prefix}；共 {len(self.targets)} 个志愿，"
            f"仅 {self.confirmed_ids} 个课程/教学班编号完全对照。"
        )
        rows = [
            f"{i+1}. [{item.key or '?'}] {item.course_id or '未填写课程代码'}"
            f" / {item.class_id or '未填写教学班'}：{item.description}"
            + (f"（相同课程候选 {item.candidates} 个）"
               if item.candidates and item.status in (
                   "class_id_missing", "class_unmatched", "class_unknown")
               else "")
            for i, item in enumerate(self.targets[:30])
        ]
        return "\n".join((
            head, "说明：开课计划仅为只读快照；编号相符不等于"
            "选课资格通过、开放时有余量或已经选中。",
            *rows,
        ))


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


def _term_key(local: str) -> tuple[str, str] | None:
    if not isinstance(local, str):
        return None
    match = _TERM.fullmatch(local.strip())
    if not match:
        return None
    year, following, semester = match.groups()
    if int(following) != int(year) + 1:
        return None
    return year, str(int(semester) - 1)


def _target(key: str, code: str, class_id: str, status: str,
            count: int = 0) -> TargetAlignment:
    return TargetAlignment(key, code, class_id, status, count)


def compare_plan_targets(
    snapshot: PlanSnapshot, config: Mapping,
) -> PlanAlignmentReport:
    """Compare exact identifiers without altering local courses or state.

    No equality of 'skbh' visible class numbers and authoritative 'skbjdm'
    is inferred. Unknown term, absent field or duplicate rows fail closed.
    """
    if not isinstance(snapshot, PlanSnapshot) or snapshot.code != "read":
        raise ValueError("plan snapshot has not been successfully read")
    if not isinstance(config, Mapping):
        raise ValueError("expected local configuration")
    targets = config.get("courses", [])
    if not isinstance(targets, list) or len(targets) > 30 or any(
        not isinstance(item, Mapping) for item in targets
    ):
        raise ValueError("invalid local target courses")
    term = _term_key(config.get("xnxq", ""))
    if (not term or not snapshot.academic_year or
            snapshot.academic_term not in ("0", "1")):
        same_term = "unverified"
    else:
        same_term = "aligned" if term == (
            snapshot.academic_year, snapshot.academic_term) else "mismatch"
    results: list[TargetAlignment] = []
    by_course: dict[str, list[CoursePlanRow]] = {}
    for row in snapshot.rows:
        by_course.setdefault(row.code, []).append(row)
    for index, raw in enumerate(targets):
        key = str(raw.get("key", str(index + 1))).strip()[:80]
        code = str(raw.get("course_id") or "").strip()
        class_id = str(raw.get("class_id") or "").strip()
        if same_term != "aligned":
            results.append(_target(key, code, class_id,
                                   "semester_unverified" if same_term == "unverified"
                                   else "semester_mismatch"))
            continue
        if not code or any(marker in code for marker in _PLACEHOLDER):
            results.append(_target(key, code, class_id, "course_id_missing"))
            continue
        candidates = by_course.get(code, [])
        if not candidates:
            results.append(_target(key, code, class_id, "course_not_in_plan"))
            continue
        if not class_id:
            results.append(_target(key, code, class_id, "class_id_missing", len(candidates)))
            continue
        matched = [row for row in candidates if row.class_id == class_id]
        if not matched:
            status = "class_unknown" if any(not row.class_id for row in candidates) else "class_unmatched"
            results.append(_target(key, code, class_id, status, len(candidates)))
            continue
        if len(matched) > 1:
            # Duplicate plan rows do not establish a unique target mapping.
            results.append(_target(key, code, class_id, "ambiguous"))
            continue
        row = matched[0]
        desired_name = str(raw.get("course_name") or "").strip()
        desired_teacher = str(raw.get("teacher") or "").strip()
        conflicts = (
            (desired_name and row.name and _normalized(desired_name) != _normalized(row.name))
            or (desired_teacher and row.teacher and _normalized(desired_teacher) != _normalized(row.teacher))
        )
        uncertain = ((desired_name and not row.name) or
                     (desired_teacher and not row.teacher))
        results.append(_target(
            key, code, class_id,
            "metadata_conflict" if conflicts
            else "metadata_unverified" if uncertain
            else "matched_ids",
        ))
    return PlanAlignmentReport(same_term, tuple(results))
