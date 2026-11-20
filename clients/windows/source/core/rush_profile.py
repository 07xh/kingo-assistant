"""Burst cadence for the write phase, kept apart from the read poller.

The read poller owns ``monitor_policy`` (30 s floor by default): it runs for
hours, every extra read costs the school a query and costs the client nothing
but time. Grabbing is the opposite shape -- the window is tens of seconds wide,
each attempt is one small POST, and the account that is not already talking to
the server when a seat appears is the account that loses the seat.

Sharing one knob between those two cadences is why the defaults looked slow:
tightening the poller to make grabbing fast would melt the read path, so the
poller's number leaked into the write path instead. This module owns the write
path, and only the write path.

Everything here is bounded on purpose. A profile is not "as fast as possible":
it is a declared, reproducible rate (interval + per-window cap + machine-wide
in-flight cap) so that a burst can be reasoned about before it is fired.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Any, Mapping

#: Hard ceilings. Above these a "burst" stops being a burst and becomes a load
#: test; the numbers are also what keeps the pacer's arithmetic exact.
MAX_CONCURRENCY = 16
MIN_INTERVAL_FLOOR = 0.05
MAX_INTERVAL = 5.0
MAX_WINDOW_SECONDS = 3600.0
MAX_WINDOW_REQUESTS = 20_000
MAX_STAGGER_MS = 10_000
MAX_ATTEMPTS = 500
MAX_DEADLINE_SECONDS = 3600.0


class RushError(ValueError):
    """A burst profile that either cannot work or cannot be reasoned about."""


#: Named starting points. ``standard`` is the default because it is the
#: fastest profile whose per-account rate still fits in a single 60 s window
#: that a school-side rate limiter is likely to tolerate.
PRESETS: dict[str, dict[str, Any]] = {
    "safe": {
        "concurrency": 2,
        "min_interval": 1.0,
        "window_seconds": 60.0,
        "window_requests": 60,
        "stagger_ms": 400,
        "deadline_seconds": 600.0,
    },
    "standard": {
        "concurrency": 8,
        "min_interval": 0.25,
        "window_seconds": 60.0,
        "window_requests": 150,
        "stagger_ms": 120,
        "deadline_seconds": 180.0,
    },
    "extreme": {
        "concurrency": 16,
        "min_interval": 0.08,
        "window_seconds": 60.0,
        "window_requests": 600,
        "stagger_ms": 40,
        "deadline_seconds": 120.0,
    },
}

_FIELDS = (
    "preset", "concurrency", "min_interval", "window_seconds", "window_requests",
    "stagger_ms", "max_attempts", "backoff_base", "backoff_cap",
    "backoff_jitter", "circuit_breaker", "deadline_seconds", "retry_after_cap",
    "drive_read_cadence",
)


def _number(raw: Any, label: str, *, low: float, high: float) -> float:
    if type(raw) not in (int, float) or isinstance(raw, bool):
        raise RushError(f"{label}必须是数字")
    value = float(raw)
    if not math.isfinite(value) or not low <= value <= high:
        raise RushError(f"{label}必须在 {low:g}–{high:g} 之间")
    return value


def _integer(raw: Any, label: str, *, low: int, high: int) -> int:
    if type(raw) is not int or isinstance(raw, bool):
        raise RushError(f"{label}必须是整数")
    if not low <= raw <= high:
        raise RushError(f"{label}必须在 {low}–{high} 之间")
    return raw


@dataclass(frozen=True, slots=True)
class RushProfile:
    """Declared burst: machine-wide in-flight cap plus per-account pacing.

    ``concurrency`` bounds how many sends are in flight across the whole
    process (all accounts together). ``min_interval`` bounds one account's
    spacing between sends, and ``window_requests``/``window_seconds`` bounds
    its total inside any rolling window. The effective interval is the larger
    of ``min_interval`` and the window's own average, so a profile cannot
    declare a window it would immediately overshoot.
    """

    preset: str = "standard"
    concurrency: int = 8
    min_interval: float = 0.25
    window_seconds: float = 60.0
    window_requests: int = 150
    stagger_ms: int = 120
    max_attempts: int = 60
    backoff_base: float = 0.25
    backoff_cap: float = 4.0
    backoff_jitter: float = 0.2
    circuit_breaker: int = 8
    deadline_seconds: float = 180.0
    retry_after_cap: float = 30.0
    #: Let this profile also drive the read poller during the burst. Off by
    #: default: the 30 s read floor exists because polling runs for hours, and
    #: a grab is the only case where reading that slowly loses the seat.
    drive_read_cadence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.preset, str) or not 1 <= len(self.preset) <= 40:
            raise RushError("档位名称必须是 1–40 个字符")
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= MAX_CONCURRENCY:
            raise RushError(f"并发数必须是 1–{MAX_CONCURRENCY} 的整数")
        if type(self.window_requests) is not int or not 1 <= self.window_requests <= MAX_WINDOW_REQUESTS:
            raise RushError(f"窗口请求数必须是 1–{MAX_WINDOW_REQUESTS} 的整数")
        if type(self.stagger_ms) is not int or not 0 <= self.stagger_ms <= MAX_STAGGER_MS:
            raise RushError(f"错峰间隔必须是 0–{MAX_STAGGER_MS} 毫秒")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise RushError(f"单目标最大尝试次数必须是 1–{MAX_ATTEMPTS}")
        if type(self.circuit_breaker) is not int or not 1 <= self.circuit_breaker <= 200:
            raise RushError("熔断阈值必须是 1–200 次连续失败")
        _number(self.min_interval, "最小间隔", low=MIN_INTERVAL_FLOOR, high=MAX_INTERVAL)
        _number(self.window_seconds, "窗口长度", low=0.1, high=MAX_WINDOW_SECONDS)
        _number(self.deadline_seconds, "突发截止时间", low=1.0, high=MAX_DEADLINE_SECONDS)
        base = _number(self.backoff_base, "退避基数", low=0.001, high=30.0)
        cap = _number(self.backoff_cap, "退避上限", low=0.001, high=120.0)
        if cap < base:
            raise RushError("退避上限不得小于退避基数")
        _number(self.backoff_jitter, "退避抖动", low=0.0, high=1.0)
        _number(self.retry_after_cap, "Retry-After 上限", low=0.0, high=600.0)
        if type(self.drive_read_cadence) is not bool:
            raise RushError("drive_read_cadence 必须是布尔值")

    @property
    def effective_interval(self) -> float:
        """Smallest spacing that satisfies both the interval and the window."""
        average = self.window_seconds / self.window_requests
        return max(self.min_interval, average)

    @property
    def requests_per_minute(self) -> float:
        """Sustained per-account rate, the window cap included."""
        by_interval = 60.0 / self.effective_interval
        by_window = self.window_requests * (60.0 / self.window_seconds)
        return min(by_interval, by_window)

    @property
    def peak_rps(self) -> float:
        return 1.0 / self.effective_interval

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update({
            "effective_interval": round(self.effective_interval, 6),
            "requests_per_minute": round(self.requests_per_minute, 3),
            "peak_rps": round(self.peak_rps, 6),
        })
        return payload

    def describe(self) -> str:
        return (
            f"档位 {self.preset}：并发 {self.concurrency}，单账号最小间隔 "
            f"{self.effective_interval:g} s，每 {self.window_seconds:g} s 最多 "
            f"{self.window_requests} 次 → 单账号约 {self.requests_per_minute:.0f} 次/分"
            f"（峰值 {self.peak_rps:.2f} rps），错峰 {self.stagger_ms} ms，"
            f"截止 {self.deadline_seconds:g} s"
        )

    def with_overrides(self, **overrides: Any) -> "RushProfile":
        clean = {key: value for key, value in overrides.items()
                 if value is not None and key in _FIELDS}
        return replace(self, **clean)


def from_mapping(raw: Mapping[str, Any] | None, *, preset: str | None = None) -> RushProfile:
    """Build a profile from ``rush:`` config, rejecting unknown keys.

    Unknown keys are a hard error: ``min_interval_ms`` silently ignored because
    the real key is ``min_interval`` is how a config looks fast and runs slow.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise RushError("rush 配置必须是映射")
    unknown = set(raw) - set(_FIELDS) - {"windows"}
    if unknown:
        raise RushError(f"rush 配置存在未知键：{', '.join(sorted(unknown))}")
    chosen = preset or raw.get("preset") or "standard"
    if chosen not in PRESETS:
        raise RushError(f"未知档位 {chosen}，可选：{'/'.join(sorted(PRESETS))}")
    base = RushProfile(preset=chosen, **PRESETS[chosen])
    overrides: dict[str, Any] = {}
    for key in ("concurrency", "window_requests", "stagger_ms", "max_attempts",
                "circuit_breaker"):
        if raw.get(key) is not None:
            overrides[key] = raw[key]
    for key in ("min_interval", "window_seconds", "backoff_base", "backoff_cap",
                "backoff_jitter", "deadline_seconds", "retry_after_cap"):
        if raw.get(key) is not None:
            overrides[key] = raw[key]
    if raw.get("drive_read_cadence") is not None:
        # Passed through untouched so a string like "false" fails loudly in
        # __post_init__ instead of becoming a truthy boolean.
        overrides["drive_read_cadence"] = raw["drive_read_cadence"]
    profile = base.with_overrides(**overrides)
    # ``windows: [[秒, 次数], ...]`` is accepted as shorthand for the single
    # rolling window, because that is the shape server_policy already uses.
    windows = raw.get("windows")
    if windows is not None:
        if (not isinstance(windows, (list, tuple)) or len(windows) != 1
                or not isinstance(windows[0], (list, tuple)) or len(windows[0]) != 2):
            raise RushError("rush.windows 目前只接受一个 [秒数, 请求数] 窗口")
        seconds, count = windows[0]
        profile = profile.with_overrides(window_seconds=seconds,
                                         window_requests=count)
    return profile


def baseline_requests_per_minute(serial_interval: float = 30.0) -> float:
    """The old single-flight cadence, for side-by-side comparison output."""
    if serial_interval <= 0:
        raise RushError("串行基线间隔必须为正数")
    return 60.0 / serial_interval


def speedup(profile: RushProfile, *, serial_interval: float = 30.0,
            accounts: int = 1) -> float:
    """How many times more attempts a burst gets, per window, versus serial."""
    if type(accounts) is not int or accounts < 1:
        raise RushError("账号数必须是正整数")
    serial = baseline_requests_per_minute(serial_interval)
    return (profile.requests_per_minute * accounts) / serial
