"""Offline-only scheduled official-portal launch preparation.

This is a manual enrollment fallback, NOT a network scraper or enrollment
adapter. It validates a user-confirmed China-local start time, then the GUI
uses the operating system's browser to open the published school homepage.
No HTTP calls, credentials, selectors or course submissions are performed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import re
import time
from typing import Mapping

CHINA_TIMEZONE = timezone(timedelta(hours=8))
MAX_SCHEDULE_HORIZON_SECONDS = 120 * 24 * 3600
PRELOGIN_LEAD_SECONDS = 15 * 60  # browser opening only; not authentication
_TIME_FORMAT = re.compile(r"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\Z")
_PLACEHOLDER_IDS = frozenset({"FILL_FROM_OFFICIAL_PAGE", "SECOND_OPTION_ID"})


@dataclass(frozen=True)
class OfficialPortalAppointment:
    """A single, explicitly confirmed browser-opening appointment."""

    start_at_epoch: float
    local_start: str
    target_count: int

    def seconds_remaining(self, now_epoch: float) -> int:
        return max(0, math.ceil(self.start_at_epoch - now_epoch))

    def is_due(self, now_epoch: float) -> bool:
        return now_epoch >= self.start_at_epoch

    def prelogin_is_due(self, now_epoch: float) -> bool:
        """Offer an early official browser login once in the final 15 minutes."""
        return (self.start_at_epoch - PRELOGIN_LEAD_SECONDS <= now_epoch
                < self.start_at_epoch)


def prepare_appointment(
    config: Mapping, *, now_epoch: float | None = None
) -> OfficialPortalAppointment:
    """Use only local settings and public course metadata; never network."""
    strategy = config.get("strategy", {})
    raw = strategy.get("target_time") if isinstance(strategy, dict) else None
    if not isinstance(raw, str) or not _TIME_FORMAT.fullmatch(raw):
        raise ValueError("请先在设置中填写正式公布的时间：YYYY-MM-DD HH:MM:SS")
    try:
        local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        timestamp = local.replace(tzinfo=CHINA_TIMEZONE).timestamp()
    except (OverflowError, ValueError) as exc:
        raise ValueError("选课日期无效，请对照学校正式通知") from exc
    now = time.time() if now_epoch is None else now_epoch
    if not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("本机时间不可用")
    if timestamp <= now:
        raise ValueError("计划时间已过去，请根据新通知修改时间")
    if timestamp - now > MAX_SCHEDULE_HORIZON_SECONDS:
        raise ValueError("预约最多提前 120 天，请先核对学校通知")
    courses = config.get("courses", [])
    if not isinstance(courses, list):
        raise ValueError("课程志愿列表格式错误")
    valid = 0
    for course in courses:
        if not isinstance(course, dict):
            raise ValueError("课程志愿列表包含无效项目")
        course_id = str(course.get("course_id") or "").strip()
        name = str(course.get("course_name") or "").strip()
        if (course_id and course_id not in _PLACEHOLDER_IDS) or name:
            valid += 1
    if valid == 0:
        raise ValueError("至少填写一门真实课程代码或课程名称，再预约官网")
    return OfficialPortalAppointment(timestamp, raw, valid)


def format_countdown(seconds: int) -> str:
    """Return a bounded countdown without including sensitive course fields."""
    if type(seconds) is not int or seconds < 0:
        raise ValueError("剩余秒数无效")
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    body = f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{days}天 {body}" if days else body
