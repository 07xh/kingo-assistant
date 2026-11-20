"""Beat the read poller down to the burst cadence, only when asked.

``monitor_policy`` keeps a 30 s floor for good reasons: monitoring runs for
hours and every read is a query the school pays for. But a seat that appears at
08:00:03 and is gone by 08:00:40 cannot be seen by a 30 s poller -- it sees the
window open and by the next read the class is already full.

So the fast cadence has to be reachable, and it has to be a separate, explicit
decision rather than a side effect of tightening the monitor. ``rush:
drive_read_cadence: true`` is that decision: while it is set, the poller uses
the burst interval (jittered) instead of the declared monitor range. Turning it
off restores the previous behaviour with no state to clean up.
"""
from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any, Mapping

from core.rush_profile import RushProfile, from_mapping


@dataclass(frozen=True, slots=True)
class RushReadCadence:
    """A resolved fast read interval derived from the burst profile."""

    interval_seconds: float
    jitter: float = 0.15
    source: str = "rush.effective_interval"

    def __post_init__(self) -> None:
        if not 0.05 <= self.interval_seconds <= 3600.0:
            raise ValueError("读取间隔必须在 0.05–3600 秒之间")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("抖动比例必须在 0–1 之间")

    def next_interval(self, rng: random.Random | None = None) -> float:
        """Jittered interval; never returns zero, never exceeds +jitter."""
        picker = rng or random
        spread = self.interval_seconds * self.jitter
        return max(0.05, self.interval_seconds
                   + picker.uniform(-spread, spread))

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_seconds": round(self.interval_seconds, 4),
            "jitter": self.jitter,
            "source": self.source,
        }


def rush_of(config: Mapping[str, Any] | None) -> RushProfile | None:
    """Resolve the ``rush:`` block from either the root or ``strategy``."""
    if not isinstance(config, Mapping):
        return None
    raw = config.get("rush")
    if raw is None:
        strategy = config.get("strategy")
        if isinstance(strategy, Mapping):
            raw = strategy.get("rush")
    if not isinstance(raw, Mapping):
        return None
    return from_mapping(raw)


def build_rush_read(config: Mapping[str, Any] | None) -> RushReadCadence | None:
    """Return a cadence only when the config explicitly asks for one."""
    profile = rush_of(config)
    if profile is None or profile.drive_read_cadence is not True:
        return None
    return RushReadCadence(
        interval_seconds=min(profile.effective_interval, 60.0),
        jitter=profile.backoff_jitter,
        source=f"rush[{profile.preset}]",
    )
