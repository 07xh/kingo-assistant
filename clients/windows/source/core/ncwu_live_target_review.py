"""Compare fresh S202 *display* rows to local course preferences, offline.

This is not an enrollment or availability matcher. S202 table 2568 exposes
`head_skbh` (display teaching-class number) and `head_kcdm` (course code).
Local `class_id` is an authoritative teaching-class ID that may instead
be `skbjdm`. Even byte-for-byte equality between these two different
field types is NOT considered a verified teaching-class identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.ncwu_live_elective import LiveElectiveSnapshot
from core.ncwu_plan_alignment import _term_key

_STATUS_LABELS = {
    "missing_target": "课程代码为空或仍为模板值",
    "semester_unverified": "本轮次学期尚未得到服务器确认，仅能查看本次页面",
    "semester_mismatch": "学校当前正选学期与本地目标学期不同",
    "not_seen": "这次页面没有出现课程代码，不能推定整个学期没有该课",
    "metadata_conflict": "代码出现，但名称或教师与本地目标不一致",
    "metadata_unknown": "代码出现，但学校页面没有足够名称/教师资料",
    "candidate": "找到相符的课程候选；教学班真实标识尚未核对",
}


@dataclass(frozen=True)
class LiveTargetItem:
    key: str
    course_code: str
    configured_class_id: str
    status: str
    visible_courses: int = 0
    matching_rows: tuple[int, ...] = ()

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self.status]


@dataclass(frozen=True)
class LiveTargetReport:
    semester_state: str
    targets: tuple[LiveTargetItem, ...]
    row_marks: tuple[tuple[str, ...], ...]

    @property
    def candidate_targets(self) -> int:
        return sum(item.status == "candidate" for item in self.targets)

    @property
    def message(self) -> str:
        if not self.targets:
            return (
                "本机没有目标课程；可以在「编辑课程与监测设置」设置课程代码。"
                "本次查询只读，不会生成志愿或提交课程。"
            )
        term = {
            "aligned": "服务器正选轮次学期与本地配置相符",
            "unverified": "服务器轮次学期尚不能核对，本次只展示课程代码线索",
            "mismatch": "服务器正选轮次学期与本地目标不同，本次不关联志愿",
        }[self.semester_state]
        head = (
            f"{term}；共 {len(self.targets)} 个本地志愿，"
            f"{self.candidate_targets} 个找到课程候选（非教学班确认）。"
        )
        rows = [
            f"{index+1}. [{target.key or '?'}] {target.course_code or '未填写'}"
            f" / {target.configured_class_id or '未填教学班 ID'}：{target.label}"
            + (f"（可见同代码 {target.visible_courses} 条）"
               if target.visible_courses else "")
            for index, target in enumerate(self.targets[:30])
        ]
        return "\n".join((
            head,
            "本次 S202 2568 列表仅观察到显示班号 skbh，"
            "不能据此验证本地 class_id/skbjdm；"
            "列表也可能分页。没有证明名额、预检或提交成功。",
            *rows,
        ))


def _comparable(expected: str, observed: str) -> bool | None:
    """None denotes unknown server metadata, False a true mismatch."""
    if not expected:
        return True
    if not observed:
        return None
    return " ".join(expected.split()).casefold() == (
        " ".join(observed.split()).casefold())


def compare_live_targets(snapshot: LiveElectiveSnapshot,
                         config: Mapping) -> LiveTargetReport:
    """No I/O and no mutations; row indexes correspond to S202 table rows."""
    if not isinstance(snapshot, LiveElectiveSnapshot) or snapshot.code != "catalog_read":
        raise ValueError("no validated S202 catalog snapshot")
    if not isinstance(config, Mapping):
        raise ValueError("invalid local preference configuration")
    courses = config.get("courses", [])
    if not isinstance(courses, list) or len(courses) > 30 or any(
        not isinstance(item, Mapping) for item in courses
    ):
        raise ValueError("invalid target preferences")

    local_term = _term_key(config.get("xnxq", ""))
    actual_term = (snapshot.academic_year, snapshot.academic_term)
    if not local_term or not all(actual_term):
        semester_state = "unverified"
    elif actual_term != local_term:
        semester_state = "mismatch"
    else:
        semester_state = "aligned"

    row_marks: list[list[str]] = [[] for _ in snapshot.courses]
    targets: list[LiveTargetItem] = []
    for index, raw in enumerate(courses):
        key = str(raw.get("key") or index + 1).strip()[:60]
        code = str(raw.get("course_id") or "").strip()
        class_id = str(raw.get("class_id") or "").strip()
        if not code or "FILL_FROM_" in code or "SECOND_OPTION_ID" in code:
            targets.append(LiveTargetItem(key, code, class_id, "missing_target"))
            continue
        if semester_state == "mismatch":
            targets.append(LiveTargetItem(key, code, class_id, "semester_mismatch"))
            continue
        matches = [
            (row_id, record) for row_id, record in enumerate(snapshot.courses)
            if record.course_code == code
        ]
        if not matches:
            targets.append(LiveTargetItem(
                key, code, class_id, "not_seen" if semester_state == "aligned"
                else "semester_unverified"))
            continue

        acceptable: list[int] = []
        has_unknown = False
        for row_id, record in matches:
            name = _comparable(
                str(raw.get("course_name") or "").strip(),
                record.course_name,
            )
            teacher = _comparable(
                str(raw.get("teacher") or "").strip(),
                record.teacher,
            )
            if name is False or teacher is False:
                continue
            if name is None or teacher is None:
                has_unknown = True
                continue
            acceptable.append(row_id)
        if semester_state == "unverified":
            status = "semester_unverified"
        elif acceptable:
            status = "candidate"
        elif has_unknown:
            status = "metadata_unknown"
        else:
            status = "metadata_conflict"
        if status == "candidate":
            for row_id in acceptable:
                row_marks[row_id].append(key)
        targets.append(LiveTargetItem(
            key, code, class_id, status, len(matches),
            tuple(acceptable) if status == "candidate" else (),
        ))
    return LiveTargetReport(
        semester_state=semester_state,
        targets=tuple(targets),
        row_marks=tuple(tuple(tags) for tags in row_marks),
    )
