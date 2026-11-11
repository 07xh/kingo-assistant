"""Explicit, bounded read-only polling policy.

No access control can be bypassed by this configuration.  When a school-specific
minimum is unknown, default to one catalog read per 60 seconds at most.  A
manual policy declaration is not independent proof of institutional permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

DEFAULT_MIN_INTERVAL = 60.0
ABSOLUTE_MIN_INTERVAL = 30.0
MAX_INTERVAL = 3600.0
MAX_DURATION_MINUTES = 1440
MAX_CYCLES = 10_000


@dataclass(frozen=True)
class MonitorLimits:
    low: float
    high: float
    max_cycles: int  # 0 means no count limit; duration limit ALWAYS applies
    max_duration_minutes: int


def validate_limits(strategy: dict, base_url: str = "") -> MonitorLimits:
    """Validate locally; does not contact or certify the school's server."""
    if not isinstance(strategy, dict):
        raise ValueError("缺少监测设置")
    values = strategy.get("interval_range")
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError("查询间隔应为 [下限, 上限]")
    if any(type(x) not in (float, int) for x in values):
        raise ValueError("查询间隔必须是有效数字")
    low, high = (float(v) for v in values)
    if not math.isfinite(low) or not math.isfinite(high):
        raise ValueError("查询间隔不能是无穷大或 NaN")

    policy = strategy.get("official_frequency_policy", {})
    if not isinstance(policy, dict):
        raise ValueError("频率规则结构无效")
    minimum = DEFAULT_MIN_INTERVAL
    if policy.get("manually_confirmed") is True:
        source = policy.get("source", "")
        value = policy.get("min_interval_seconds")
        if not isinstance(source, str) or not source.strip() or len(source) > 500:
            raise ValueError("调整学校最低查询间隔前需记录官方规定的出处")
        if type(value) not in (float, int):
            raise ValueError("学校最低间隔必须是有效数字")
        try:
            approved = float(value)
        except OverflowError as exc:
            raise ValueError("学校最低间隔超出支持范围") from exc
        if not math.isfinite(approved):
            raise ValueError("学校最低间隔必须是有效数字")
        if not ABSOLUTE_MIN_INTERVAL <= approved <= MAX_INTERVAL:
            raise ValueError("官方允许的最低间隔必须在 30 至 3600 秒范围内")
        minimum = approved
    elif policy:  # A partial declaration is not proof of a lower safe interval.
        if policy.get("manually_confirmed") not in (False, None):
            raise ValueError("频率规则的人工确认状态无效")

    if low < ABSOLUTE_MIN_INTERVAL:
        # Experiments or host-scoped approval records are explicitly opted into;
        # neither implies the university approved these hypothetical rates.
        from .polling_policy import validate_rate_permission
        validate_rate_permission(strategy, base_url)
        if policy.get("manually_confirmed") is not True:
            minimum = low
    if not minimum <= low <= high <= MAX_INTERVAL:
        raise ValueError(f"查询间隔需在 {minimum:g} 至 3600 秒之间，且下限不能大于上限")

    cycles = strategy.get("max_cycles")
    if type(cycles) is not int or not 0 <= cycles <= MAX_CYCLES:
        raise ValueError("查询次数必须为 0–10000（0 表示按时长持续监测）")
    duration = strategy.get("max_duration_minutes", 480)
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_MINUTES:
        raise ValueError("监测时长必须为 1–1440 分钟")
    return MonitorLimits(low, high, cycles, duration)
