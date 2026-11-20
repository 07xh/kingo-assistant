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
#: Lowest floor an operator may declare at all. Anything below this is not a
#: "faster poll", it is a load test against a school system.
MIN_OVERRIDABLE_FLOOR = 5.0
MAX_INTERVAL = 3600.0
MAX_DURATION_MINUTES = 1440
MAX_CYCLES = 10_000


@dataclass(frozen=True)
class MonitorLimits:
    low: float
    high: float
    max_cycles: int  # 0 means no count limit; duration limit ALWAYS applies
    max_duration_minutes: int
    floor: float = ABSOLUTE_MIN_INTERVAL  # effective floor after any override

    def __post_init__(self) -> None:
        if not MIN_OVERRIDABLE_FLOOR <= self.floor <= ABSOLUTE_MIN_INTERVAL:
            raise ValueError("生效下限必须在 5 至 30 秒之间")
        if self.low < self.floor:
            raise ValueError("查询间隔下限不得低于生效下限")

    @property
    def faster_than_default_floor(self) -> bool:
        return self.floor < ABSOLUTE_MIN_INTERVAL


def _resolve_floor(policy: dict) -> float:
    """Effective minimum interval, honouring an explicitly attested override.

    The default floor stays 30 s. Lowering it is allowed only when the operator
    records BOTH ``absolute_floor_verified: true`` and a non-empty
    ``source``/``absolute_floor_source`` naming the published rule that permits
    the faster cadence. An unattested value is rejected outright rather than
    ignored, so a typo cannot silently look "safe".
    """
    raw = policy.get("absolute_floor_seconds")
    if raw is None:
        return ABSOLUTE_MIN_INTERVAL
    if policy.get("absolute_floor_verified") is not True:
        raise ValueError("调低绝对下限前需将 absolute_floor_verified 置为 true")
    reason = policy.get("absolute_floor_source") or policy.get("source")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValueError("调低绝对下限前需记录官方依据的出处")
    if type(raw) not in (float, int) or isinstance(raw, bool):
        raise ValueError("绝对下限必须是有效数字")
    try:
        value = float(raw)
    except OverflowError as exc:
        raise ValueError("绝对下限超出支持范围") from exc
    if not math.isfinite(value):
        raise ValueError("绝对下限必须是有效数字")
    if not MIN_OVERRIDABLE_FLOOR <= value < ABSOLUTE_MIN_INTERVAL:
        raise ValueError(
            f"绝对下限只能在 {MIN_OVERRIDABLE_FLOOR:g} 至 "
            f"{ABSOLUTE_MIN_INTERVAL:g} 秒之间")
    return value


def validate_limits(strategy: dict) -> MonitorLimits:
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
    floor = ABSOLUTE_MIN_INTERVAL
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
        floor = _resolve_floor(policy)
        if not floor <= approved <= MAX_INTERVAL:
            raise ValueError(
                f"官方允许的最低间隔必须在 {floor:g} 至 {MAX_INTERVAL:g} 秒范围内")
        minimum = approved
    elif policy:  # A partial declaration is not proof of a lower safe interval.
        if policy.get("manually_confirmed") not in (False, None):
            raise ValueError("频率规则的人工确认状态无效")
        if policy.get("absolute_floor_seconds") is not None:
            raise ValueError("调低绝对下限前需先完成官方频率规则的人工确认")

    if not minimum <= low <= high <= MAX_INTERVAL:
        raise ValueError(f"查询间隔需在 {minimum:g} 至 3600 秒之间，且下限不能大于上限")
    if low < floor:
        raise ValueError(f"查询间隔下限不得低于生效下限 {floor:g} 秒")

    cycles = strategy.get("max_cycles")
    if type(cycles) is not int or not 0 <= cycles <= MAX_CYCLES:
        raise ValueError("查询次数必须为 0–10000（0 表示按时长持续监测）")
    duration = strategy.get("max_duration_minutes", 480)
    if type(duration) is not int or not 1 <= duration <= MAX_DURATION_MINUTES:
        raise ValueError("监测时长必须为 1–1440 分钟")
    return MonitorLimits(low, high, cycles, duration, floor)
