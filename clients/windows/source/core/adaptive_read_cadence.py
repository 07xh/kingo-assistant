"""Optional, offline-only adaptive cadence for authorized read-only monitoring.

This controller NEVER performs requests. It only slows a previously validated
read schedule when request/budget latency indicates pressure, then gradually
recovers to its original cadence. It cannot shorten the operator's configured
interval or activate enrollment submissions.
"""
from __future__ import annotations

import math
from typing import Mapping

MAX_INTERVAL_SECONDS = 3600.0


def _positive_number(value: object, label: str, *, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{label}必须是有限数字")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须在 {minimum:g}–{maximum:g} 之间")
    return number


class AdaptiveReadCadence:
    """Adaptive *slowdown*, opt-in; no remote probing or write traffic.

    A healthy observation relaxes the slowdown toward 1x; a slow response or
    significant request-budget wait doubles it, capped by max_multiplier.
    Absence of timing evidence is not treated as proof of a fast response.
    """

    def __init__(self, settings: Mapping | None = None) -> None:
        if settings is None:
            settings = {}
        if not isinstance(settings, Mapping):
            raise ValueError("自适应只读调度配置必须是映射")
        enabled = settings.get("enabled", False)
        if type(enabled) is not bool:
            raise ValueError("自适应只读调度开关必须是布尔值")
        self.enabled = enabled
        self.multiplier = 1.0
        self.slow_samples = 0
        self.healthy_samples = 0
        # Invalid parameters fail closed if feature is explicitly enabled.
        self.slow_request_ms = (
            _positive_number(settings.get("slow_request_ms", 2000), "慢请求阈值",
                             minimum=100, maximum=60000)
            if enabled else 2000.0
        )
        self.budget_wait_ms = (
            _positive_number(settings.get("budget_wait_ms", 200), "预算等待阈值",
                             minimum=10, maximum=60000)
            if enabled else 200.0
        )
        self.max_multiplier = (
            _positive_number(settings.get("max_multiplier", 8), "最大放缓倍率",
                             minimum=1, maximum=16)
            if enabled else 8.0
        )

    @staticmethod
    def _timing_number(timing: Mapping, key: str) -> float | None:
        value = timing.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return None
        return float(value)

    def observe_success(self, timing: Mapping | None) -> None:
        """Observe a completed successful catalog read, with no extra HTTP."""
        if not self.enabled or not isinstance(timing, Mapping):
            return
        request_ms = self._timing_number(timing, "request_ms")
        budget_ms = self._timing_number(timing, "budget_wait_ms")
        if request_ms is None or budget_ms is None:
            return
        if request_ms >= self.slow_request_ms or budget_ms >= self.budget_wait_ms:
            self.slow_samples += 1
            self.multiplier = min(self.max_multiplier, self.multiplier * 2.0)
        else:
            self.healthy_samples += 1
            self.multiplier = max(1.0, self.multiplier * 0.75)

    def interval(self, baseline: float, *, floor: float) -> float:
        """Never run faster than the original policy-validated baseline."""
        base = _positive_number(baseline, "原始查询间隔", minimum=0.001,
                                maximum=MAX_INTERVAL_SECONDS)
        minimum = _positive_number(floor, "已验证间隔下限", minimum=0.001,
                                   maximum=MAX_INTERVAL_SECONDS)
        if base < minimum:
            raise ValueError("原始查询间隔不得低于请求策略下限")
        if not self.enabled:
            return base
        return max(minimum, min(MAX_INTERVAL_SECONDS, base * self.multiplier))

    def report(self) -> dict:
        """Credential-free diagnostics for future UI presentation."""
        return {
            "enabled": self.enabled,
            "slowdown_multiplier": round(self.multiplier, 3),
            "slow_samples": self.slow_samples,
            "healthy_samples": self.healthy_samples,
        }
