"""Read-only current-result candidates against the user's local course plan.

This module never marks an enrollment as confirmed. Historical HTML result
tables can show internal class IDs and a selected-looking status without
proving the *current selection round* or an authoritative server write.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core.ncwu_known_result_candidates import HistoricalClassCandidate

_LABELS = {
    "invalid_target": "本地志愿缺少有效课程或内部教学班 ID",
    "not_seen": "本次结果页未出现该课程；不代表当前未选",
    "different_class": "结果页存在同课程但不同内部教学班",
    "multiple_classes": "同课程存在多个内部教学班候选，需人工核对",
    "status_unverified": "精确教学班候选存在，但状态组合未验收",
    "scope_unverified": "精确候选存在，但本次账号或学期尚未绑定",
    "observed_pair": "精确教学班候选及历史状态组合相符，仍未确认选课",
}


@dataclass(frozen=True, slots=True)
class ResultTargetMatch:
    key: str
    state: str
    candidate_rows: tuple[int, ...] = ()

    @property
    def label(self) -> str:
        return _LABELS[self.state]


@dataclass(frozen=True, slots=True)
class ResultTargetReview:
    targets: tuple[ResultTargetMatch, ...]
    row_marks: tuple[tuple[str, ...], ...]
    session_scope_checked: bool

    @property
    def matched_candidates(self) -> int:
        return sum(t.state == "observed_pair" for t in self.targets)

    @property
    def message(self) -> str:
        head = (
            f"志愿 {len(self.targets)} 条，"
            f"精确教学班及历史状态组合候选 {self.matched_candidates} 条。"
        )
        scope = (
            "本次 GET 参数已与当前 EXE 登录账号及目标学期核对。"
            if self.session_scope_checked
            else "尚未完成当前 EXE 账号及目标学期的双重核验。"
        )
        lines = [
            head, scope,
            "历史 xk_status_m=03 / 选中 仅为页面状态组合。"
            "不能推定当前选课轮次、服务器提交确认或正式选课成功。",
        ]
        lines.extend(
            f"{index}. 志愿 {entry.key}：{entry.label}"
            for index, entry in enumerate(self.targets, start=1)
        )
        return "\n".join(lines)


def compare_result_targets(
    candidates: tuple[HistoricalClassCandidate, ...],
    config: Mapping, *, account_bound: bool, semester_bound: bool,
) -> ResultTargetReview:
    """Pure comparison: exact course ID/class ID, never a success verdict."""
    if (not isinstance(candidates, tuple)
            or len(candidates) > 200
            or any(not isinstance(v, HistoricalClassCandidate) for v in candidates)
            or not isinstance(config, Mapping)):
        raise ValueError("候选数据或本机配置不符合预期")
    courses = config.get("courses", ())
    if (not isinstance(courses, (list, tuple)) or len(courses) > 30
            or any(not isinstance(c, Mapping) for c in courses)):
        raise ValueError("课程志愿格式异常")
    scoped = account_bound is True and semester_bound is True
    marks: list[list[str]] = [[] for _ in candidates]
    targets: list[ResultTargetMatch] = []
    for number, course in enumerate(courses, 1):
        key = course.get("key")
        key = str(key)[:50] if key else str(number)
        code, klass = course.get("course_id"), course.get("class_id")
        if (not isinstance(code, str) or not isinstance(klass, str)
                or not code.strip() or not klass.strip()
                or code.startswith("FILL_FROM_")
                or klass.startswith("FILL_FROM_")
                or code in ("SECOND_OPTION_ID",)
                or klass in ("SECOND_OPTION_ID",)
                or code != code.strip() or klass != klass.strip()):
            targets.append(ResultTargetMatch(key, "invalid_target"))
            continue
        same_course = tuple(
            i for i, row in enumerate(candidates) if row.course_code == code)
        exact = tuple(
            i for i in same_course if candidates[i].internal_class_id == klass)
        if not same_course:
            state = "not_seen"
        elif not exact:
            state = "different_class"
        elif len(same_course) != len(exact) or len(exact) != 1:
            state = "multiple_classes"
        elif not scoped:
            state = "scope_unverified"
        elif not candidates[exact[0]].observed_selected_pair:
            state = "status_unverified"
        else:
            state = "observed_pair"

        for i in exact:
            marks[i].append(f"志愿 {key}：精确候选")
        targets.append(ResultTargetMatch(key, state, exact))
    return ResultTargetReview(
        targets=tuple(targets),
        row_marks=tuple(tuple(v) for v in marks),
        session_scope_checked=scoped,
    )
