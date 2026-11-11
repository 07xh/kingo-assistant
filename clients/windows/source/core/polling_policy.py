"""Host-scoped, read-only polling policy; do not infer school limits from QPS examples."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit

INTERVAL_PRESETS = {
    "最快（60 秒）": (60, 60),
    "均衡（120 秒）": (120, 120),
    "低频（300 秒）": (300, 300),
    "参考（1.5 秒，需确认）": (1.5, 1.5),
    "加速（1 秒，需确认）": (1.0, 1.0),
    "极速（0.5 秒，需确认）": (0.5, 0.5),
}
FAST_INTERVALS = frozenset((Decimal("0.5"), Decimal("1"), Decimal("1.5")))


def _decimal_seconds(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("查询间隔必须是合法数值")
    raw = str(value).strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw, flags=re.ASCII) is None:
        raise ValueError("查询间隔必须是标准非负数字秒数")
    try:
        value = Decimal(raw)
    except InvalidOperation as error:
        raise ValueError("查询间隔数值格式错误") from error
    if not value.is_finite():
        raise ValueError("查询间隔不可为无穷或非数字")
    return value


def parse_interval_range(low, high) -> tuple[int | float, int | float]:
    """Preserve original integer custom range; fast values only as named presets."""
    start, end = _decimal_seconds(low), _decimal_seconds(high)
    def allowed(value: Decimal) -> bool:
        return value in FAST_INTERVALS or (
            value == value.to_integral_value() and 60 <= value <= 3600)
    if not allowed(start) or not allowed(end) or start > end:
        raise ValueError("查询间隔限 0.5/1/1.5 秒条件档或 60–3600 整数秒，且下限不大于上限")
    def normalized(value: Decimal) -> int | float:
        return float(value) if value < 60 else int(value)
    return normalized(start), normalized(end)


def validate_rate_permission(strategy: dict, base_url: str) -> tuple[int | float, int | float]:
    """Fast monitoring requires either a scoped approval record or user opt-in.

    User opt-in is NOT evidence of institutional permission or a safe server rate.
    Users remain responsible for complying with the institution's rules.
    """
    raw = strategy.get("interval_range")
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError("strategy.interval_range 必须有上下限")
    low, high = parse_interval_range(raw[0], raw[1])
    if low >= 60:
        return low, high
    record = strategy.get("rate_permission")
    # Explicitly recorded institutional restrictions take precedence; a checkbox
    # must NEVER be able to bypass a documented stricter site-specific limit.
    if record is None:
        if strategy.get("fast_polling_opt_in") is True:
            return low, high
        raise ValueError("低于 60 秒需明确勾选实验模式；校方频率许可尚未确认")
    host = urlsplit(base_url).hostname
    if (not isinstance(record, dict) or record.get("confirmed") is not True
            or record.get("host") != host
            or record.get("scope") != "read-only-course-catalog"):
        raise ValueError("低于 60 秒须核实校方许可，且仅限获许可域名和只读课程查询")
    evidence = record.get("evidence_ref")
    if (not isinstance(evidence, str) or not 12 <= len(evidence.strip()) <= 300
            or any(ord(ch) < 32 for ch in evidence)):
        raise ValueError("须记录可独立核实的校方书面频率许可来源")
    minimum = _decimal_seconds(record.get("minimum_interval_seconds"))
    if not Decimal("0.5") <= minimum <= 3600:
        raise ValueError("许可最小间隔必须处于 0.5–3600 秒内")
    if Decimal(str(low)) < minimum:
        raise ValueError("所选查询间隔低于校方许可的最小间隔")
    return low, high
