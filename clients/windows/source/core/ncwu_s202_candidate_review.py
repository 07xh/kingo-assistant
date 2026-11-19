"""Read-only comparison of S202 result-page candidates with local targets.

The HTML result page has unverified schema and shows a visible class number,
not a proven internal teaching-class ID. This module MUST NOT produce
EnrollmentEngine records or mark a course as officially selected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.ncwu_s202_readback import S202ReadbackResult

_MESSAGES = {
    "invalid_target": "志愿缺少真实课程代码",
    "not_seen": "本页未发现相同课程代码，不代表未选",
    "metadata_conflict": "课程代码相同，但课程名称或教师不匹配",
    "metadata_unknown": "课程代码相同，但缺少可核对的名称或教师信息",
    "candidate": "找到单个课程候选，内部教学班 ID 仍未核验",
    "ambiguous": "出现多个课程候选，不能确定教学班",
}


@dataclass(frozen=True)
class CandidateTargetMatch:
    key: str
    course_code: str
    configured_internal_class_id: str
    state: str
    matching_rows: tuple[int, ...] = ()

    @property
    def label(self) -> str:
        return _MESSAGES[self.state]


@dataclass(frozen=True)
class CandidateTargetReport:
    targets: tuple[CandidateTargetMatch, ...]
    row_marks: tuple[tuple[str, ...], ...]

    @property
    def candidate_count(self) -> int:
        return sum(t.state == "candidate" for t in self.targets)

    @property
    def ambiguous_count(self) -> int:
        return sum(t.state == "ambiguous" for t in self.targets)

    @property
    def message(self) -> str:
        heading = (
            f"本地志愿 {len(self.targets)} 条；单行课程候选 {self.candidate_count} 条，"
            f"多行歧义 {self.ambiguous_count} 条（均未确认已选）。"
        )
        details = [
            f"{idx}. [{item.key}] {item.course_code or '未知课程'}：{item.label}"
            for idx, item in enumerate(self.targets, 1)
        ]
        return "\n".join((
            heading,
            "只能按课程代码、课程名、教师生成本地查看线索；不能把页面中的"
            "显示班号视为内部 skbjdm，也没有验证账号、学期、服务端提交结果。",
            "任何候选匹配都不能转换成正式已选记录或成功状态。",
            *details,
        ))


def _comparable(expected: str, actual: str) -> bool | None:
    if not expected:
        return True
    if not actual:
        return None
    return " ".join(expected.split()).casefold() == (
        " ".join(actual.split()).casefold())


def compare_s202_candidate_targets(
    result: S202ReadbackResult, config: Mapping,
) -> CandidateTargetReport:
    """Pure in-memory candidate linkage; no HTTP, state mutation or write."""
    if not isinstance(result, S202ReadbackResult) or result.code != "page_observed":
        raise ValueError("缺少当前有效的已选结果候选列表")
    if not isinstance(config, Mapping):
        raise ValueError("本地志愿配置不是映射")
    planned = config.get("courses", [])
    if not isinstance(planned, list) or len(planned) > 30 or any(
        not isinstance(item, Mapping) for item in planned
    ):
        raise ValueError("本地志愿配置异常")

    marks: list[list[str]] = [[] for _ in result.candidates]
    targets: list[CandidateTargetMatch] = []
    for index, raw in enumerate(planned, 1):
        key = str(raw.get("key") or index).strip()[:60]
        course = str(raw.get("course_id") or "").strip()
        internal = str(raw.get("class_id") or "").strip()
        if not course or "FILL_FROM_" in course or "SECOND_OPTION_ID" in course:
            targets.append(CandidateTargetMatch(key, course, internal, "invalid_target"))
            continue
        course_rows = [
            (i, row) for i, row in enumerate(result.candidates)
            if row.course_code == course
        ]
        if not course_rows:
            targets.append(CandidateTargetMatch(key, course, internal, "not_seen"))
            continue

        possible: list[int] = []
        missing_metadata = False
        for row_id, row in course_rows:
            name_check = _comparable(
                str(raw.get("course_name") or "").strip(), row.course_name)
            teacher_check = _comparable(
                str(raw.get("teacher") or "").strip(), row.teacher)
            if name_check is False or teacher_check is False:
                continue
            if name_check is None or teacher_check is None:
                missing_metadata = True
                continue
            possible.append(row_id)
        if len(possible) > 1:
            state = "ambiguous"
        elif possible:
            state = "candidate"
        elif missing_metadata:
            state = "metadata_unknown"
        else:
            state = "metadata_conflict"
        if state in ("candidate", "ambiguous"):
            for row_id in possible:
                marks[row_id].append(f"课程候选：{key}")
        targets.append(CandidateTargetMatch(
            key, course, internal, state, tuple(possible)))
    return CandidateTargetReport(
        tuple(targets), tuple(tuple(row) for row in marks))
