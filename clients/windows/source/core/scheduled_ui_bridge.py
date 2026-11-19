"""Offline admission bridge from Windows course plans to ScheduledEnrollmentRunner.

This module prepares typed targets and a verified-actor-ready runner without
creating a school HTTP client, inferring open selection windows, or enabling
an NCWU write adapter. A real injected EnrollmentEngine must pass its own
school, user and adapter capability gates before any wait/network work.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
import time
from typing import Mapping

from core.course_models import CourseTarget
from core.enrollment import EnrollmentEngine, _validate_targets
from core.enrollment_schedule import ScheduledEnrollmentRunner, ScheduledRunReport, validate_schedule_limits
from core.time_sync import TimeSync

CHINA_TIMEZONE = timezone(timedelta(hours=8))
_MAX_SCHEDULE_HORIZON_SECONDS = 120 * 24 * 3600
_TIME_FORMAT = re.compile(r"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z")
_TEMPLATE_IDS = frozenset({"FILL_FROM_OFFICIAL_PAGE", "SECOND_OPTION_ID"})


def validate_target_time(config: Mapping, *, now_epoch: float | None = None) -> str:
    """Validate China-local selection time without a browser appointment."""
    strategy = config.get("strategy")
    raw = strategy.get("target_time") if isinstance(strategy, dict) else None
    if not isinstance(raw, str) or not _TIME_FORMAT.fullmatch(raw):
        raise ValueError("请填写正式确认的时间：YYYY-MM-DD HH:MM:SS")
    try:
        local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        timestamp = local.replace(tzinfo=CHINA_TIMEZONE).timestamp()
    except (OverflowError, ValueError) as exc:
        raise ValueError("选课日期无效，请对照学校正式通知") from exc
    now = time.time() if now_epoch is None else now_epoch
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("本机时间不可用")
    if timestamp <= now:
        raise ValueError("计划时间已过去，请根据新通知修改时间")
    if timestamp - now > _MAX_SCHEDULE_HORIZON_SECONDS:
        raise ValueError("计划时间最多提前 120 天，请核对学校通知")
    return raw


@dataclass(frozen=True)
class PreparedScheduledPlan:
    """Local plan only; never evidence that enrollment is enabled."""

    target_time: str
    targets: tuple[CourseTarget, ...]
    poll_interval_range: tuple[int, int]
    max_wait_minutes: int


def prepare_scheduled_plan(
    config: Mapping, *, now_epoch: float | None = None,
) -> PreparedScheduledPlan:
    """Validate exact course/class identities and a user-confirmed future time."""
    if not isinstance(config, Mapping):
        raise ValueError("自动选课计划配置必须为映射对象")
    target_time = validate_target_time(config, now_epoch=now_epoch)
    semester = config.get("xnxq")
    if not isinstance(semester, str) or not semester.strip():
        raise ValueError("请先确认目标选课学期")
    raw = config.get("courses")
    if not isinstance(raw, list):
        raise ValueError("课程志愿必须为列表")
    targets: list[CourseTarget] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第{index}个志愿结构异常")
        course_id = item.get("course_id")
        class_id = item.get("class_id")
        if not isinstance(course_id, str) or course_id in _TEMPLATE_IDS:
            raise ValueError(f"第{index}个志愿尚无核实的课程代码")
        if not isinstance(class_id, str) or not class_id.strip():
            raise ValueError(f"第{index}个志愿缺少明确的教学班编号")
        targets.append(CourseTarget(
            key=item.get("key", ""),
            course_id=course_id,
            class_id=class_id,
            group=item.get("group", ""),
            semester=semester,
            course_name=item.get("course_name", ""),
            teacher=item.get("teacher", ""),
        ))
    _validate_targets(targets, semester=semester)
    strategy = config.get("strategy")
    if not isinstance(strategy, dict):
        raise ValueError("自动选课调度配置无效")
    poll_range = strategy.get("window_poll_interval_range", (60, 60))
    wait_minutes = strategy.get("window_max_wait_minutes", 480)

    # Pure admission contract; no dummy engine, network client or clock.
    limits, validated_wait = validate_schedule_limits(poll_range, wait_minutes)
    return PreparedScheduledPlan(
        target_time, tuple(targets), limits, validated_wait,
    )


async def run_prepared_schedule(
    plan: PreparedScheduledPlan,
    engine: EnrollmentEngine,
    *,
    stop_event: asyncio.Event | None = None,
) -> ScheduledRunReport:
    """Execution seam for a future independently verified school adapter.

    The shipped Windows UI does not call this without an approved real
    EnrollmentEngine; the runner checks all capability gates before waiting.
    """
    if not isinstance(plan, PreparedScheduledPlan):
        raise ValueError("定时执行计划类型错误")
    runner = ScheduledEnrollmentRunner(
        engine, TimeSync(),
        poll_interval_range=plan.poll_interval_range,
        max_wait_minutes=plan.max_wait_minutes,
    )
    return await runner.run(plan.targets, plan.target_time, stop_event=stop_event)
