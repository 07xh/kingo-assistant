"""Selection-window boundary estimation from observe-only probes.

The engine's window gate is binary per read: ``OPEN`` / ``CLOSED`` / ``UNKNOWN``.
That is enough to refuse a write, but not enough to answer the one question that
decides a seat race: *exactly when does the server flip, relative to the
announced time?* A 60 s poll cannot see a sub-second flip, so the client either
starts too early (the server still says "not a valid selection window") or too
late (the seat is gone).

This module turns a short burst of probes around T0 into an estimate:

* the flip happened somewhere in ``(last_closed, first_open]``;
* the width of that interval is the estimate's uncertainty;
* combined with a clock offset, it yields the server-side flip instant and a
  recommended lead time for the next round.

No request is issued here. Callers own their budget, and
:class:`ProbeSchedule` refuses to emit an interval faster than the caller has
explicitly declared — the default is the client-wide 30 s floor, so this module
cannot be used to turn a compliant client into a burst client by accident.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

#: Mirrors ``monitor_policy.ABSOLUTE_MIN_INTERVAL``. A caller who wants a faster
#: probe cadence must pass a lower value explicitly, which is the point: the
#: decision is visible at the call site instead of hidden in a default.
DEFAULT_MIN_INTERVAL_MS = 30_000.0

#: Probes closer together than this are considered a duplicated observation of
#: the same instant and cannot tighten the interval any further.
MIN_MEANINGFUL_GAP_MS = 20.0

#: Hard cap so a misconfigured schedule cannot become a flood.
MAX_SCHEDULE_LENGTH = 200


class WindowPhase(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class WindowProbe:
    """One observation of the official window state.

    ``at_local`` is ``time.time()`` (epoch seconds) taken immediately before the
    response body was read, so it is an upper bound on when the server decided.
    """

    at_local: float
    phase: WindowPhase
    source: str = "prohibit_marker"

    def __post_init__(self) -> None:
        if not isinstance(self.at_local, (int, float)) or isinstance(self.at_local, bool):
            raise ValueError("探测时间必须是数值")
        if not 0 < float(self.at_local) < 4e9:
            raise ValueError("探测时间必须是秒级 epoch")
        if not isinstance(self.phase, WindowPhase):
            raise ValueError("探测状态无效")
        if not isinstance(self.source, str) or len(self.source) > 60:
            raise ValueError("探测来源标识无效")


class ProbeError(RuntimeError):
    """Probe history is unusable for estimation."""


@dataclass(frozen=True, slots=True)
class FlipEstimate:
    """Where the window flipped, and how much to trust it."""

    observed_flip_local: float
    last_closed_local: float | None
    uncertainty_ms: float
    server_flip_epoch: float | None
    recommended_lead_ms: float
    probes_used: int
    reopened: bool = False
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "observed_flip_local": round(self.observed_flip_local, 3),
            "last_closed_local": (round(self.last_closed_local, 3)
                                  if self.last_closed_local is not None else None),
            "uncertainty_ms": round(self.uncertainty_ms, 3),
            "server_flip_epoch": (round(self.server_flip_epoch, 3)
                                  if self.server_flip_epoch is not None else None),
            "recommended_lead_ms": round(self.recommended_lead_ms, 3),
            "probes_used": self.probes_used,
            "reopened": self.reopened,
            "detail": self.detail,
        }


def _sorted_open_history(probes: Sequence[WindowProbe]) -> list[WindowProbe]:
    if not probes:
        raise ProbeError("没有窗口探测记录，无法估计翻转时刻")
    ordered = sorted(probes, key=lambda p: p.at_local)
    stamps = [p.at_local for p in ordered]
    if len(set(stamps)) != len(stamps):
        raise ProbeError("探测时间戳重复，无法判断先后顺序")
    return ordered


def estimate_flip(probes: Sequence[WindowProbe], *,
                  clock_offset_ms: float | None = None,
                  network_half_rtt_ms: float = 0.0) -> FlipEstimate:
    """Locate the first CLOSED -> OPEN transition.

    ``clock_offset_ms`` is ``server_time - local_time`` in milliseconds (the
    convention used by ``selection_optim.ClockSync``). When absent, the
    server-side instant is reported as ``None`` instead of guessed.

    ``network_half_rtt_ms`` accounts for the fact that a CLOSED response may have
    been decided before the response arrived; it widens the uncertainty band,
    never narrows it.
    """
    if not isinstance(clock_offset_ms, (int, float)) or isinstance(clock_offset_ms, bool):
        if clock_offset_ms is not None:
            raise ValueError("时钟偏移必须是数值或 None")
    if not isinstance(network_half_rtt_ms, (int, float)) or network_half_rtt_ms < 0:
        raise ValueError("网络半程时间无效")
    if network_half_rtt_ms > 60_000:
        raise ValueError("网络半程时间超出合理范围")

    ordered = _sorted_open_history(probes)
    transitions: list[tuple[WindowProbe | None, WindowProbe]] = []
    previous: WindowProbe | None = None
    for probe in ordered:
        if probe.phase is WindowPhase.UNKNOWN:
            # An UNKNOWN probe cannot anchor either side of the boundary.
            continue
        if probe.phase is WindowPhase.OPEN:
            if previous is None or previous.phase is WindowPhase.CLOSED:
                transitions.append((previous if previous is not None
                                    and previous.phase is WindowPhase.CLOSED else None,
                                    probe))
            previous = probe
        else:
            previous = probe

    if not transitions:
        closed_only = [p for p in ordered if p.phase is WindowPhase.CLOSED]
        if closed_only and all(p.phase is not WindowPhase.OPEN for p in ordered):
            raise ProbeError("全部探测均为未开放，窗口尚未翻转")
        raise ProbeError("没有可用的 CLOSED -> OPEN 转变记录")

    anchor, first_open = transitions[0]
    reopened = len(transitions) > 1

    if anchor is None:
        # The window was already open on our first usable observation: the flip
        # happened at or before it, so this is a lower bound only.
        uncertainty_ms = 0.0
        detail = "首次探测即已开放，只能给出上界（翻转不晚于该时刻）"
    else:
        gap_ms = (first_open.at_local - anchor.at_local) * 1000.0
        uncertainty_ms = max(gap_ms, MIN_MEANINGFUL_GAP_MS)
        detail = f"翻转落在 {gap_ms:.0f} ms 的探测间隙内"
    uncertainty_ms += float(network_half_rtt_ms)

    server_flip = None
    if clock_offset_ms is not None:
        server_flip = first_open.at_local + float(clock_offset_ms) / 1000.0

    # Start just inside the earliest plausible flip: half the band plus the
    # request's own travel time. Never negative, never more than the band.
    recommended_lead_ms = min(uncertainty_ms, uncertainty_ms / 2.0 + network_half_rtt_ms)

    return FlipEstimate(
        observed_flip_local=first_open.at_local,
        last_closed_local=anchor.at_local if anchor is not None else None,
        uncertainty_ms=uncertainty_ms,
        server_flip_epoch=server_flip,
        recommended_lead_ms=recommended_lead_ms,
        probes_used=len(ordered),
        reopened=reopened,
        detail=detail,
    )


@dataclass(frozen=True, slots=True)
class ProbeSchedule:
    """Timestamps to probe at, around a target instant.

    ``min_interval_ms`` defaults to the client-wide 30 s floor. A denser burst is
    a policy decision that must be written at the call site and backed by a
    registered window attestation — this class will not do it silently.
    """

    target_epoch: float
    lead_ms: float = 3_000.0
    interval_ms: float = 200.0
    tail_ms: float = 2_000.0
    min_interval_ms: float = DEFAULT_MIN_INTERVAL_MS

    def __post_init__(self) -> None:
        for value, label, low, high in (
            (self.lead_ms, "提前量", 0.0, 3_600_000.0),
            (self.interval_ms, "探测间隔", MIN_MEANINGFUL_GAP_MS, 3_600_000.0),
            (self.tail_ms, "探测尾部", 0.0, 3_600_000.0),
            (self.min_interval_ms, "许可最小间隔", MIN_MEANINGFUL_GAP_MS, 3_600_000.0),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < low:
                raise ValueError(f"{label}无效")
            if value > high:
                raise ValueError(f"{label}超出合理范围")
        if self.interval_ms < self.min_interval_ms:
            raise ValueError(
                f"探测间隔 {self.interval_ms:.0f} ms 低于已声明的许可下限 "
                f"{self.min_interval_ms:.0f} ms；需要更密的探测必须在调用处"
                f"显式下调 min_interval_ms 并具备证据")
        if self._planned_count() > MAX_SCHEDULE_LENGTH:
            raise ValueError(
                f"探测计划 {self._planned_count()} 次超过上限 {MAX_SCHEDULE_LENGTH}；"
                f"缩小 lead_ms/tail_ms 或放宽 interval_ms")

    def _planned_count(self) -> int:
        span = float(self.lead_ms) + float(self.tail_ms)
        return int(round(span / float(self.interval_ms))) + 1

    def offsets_ms(self) -> tuple[float, ...]:
        """Probe offsets relative to ``target_epoch``, in milliseconds."""
        count = self._planned_count()
        if count > MAX_SCHEDULE_LENGTH:
            raise ValueError(
                f"探测计划 {count} 次超过上限 {MAX_SCHEDULE_LENGTH}")
        start = -float(self.lead_ms)
        return tuple(start + index * float(self.interval_ms)
                     for index in range(count))

    def timestamps(self) -> tuple[float, ...]:
        return tuple(self.target_epoch + offset / 1000.0 for offset in self.offsets_ms())

    def report(self) -> dict:
        offsets = self.offsets_ms()
        return {
            "target_epoch": round(self.target_epoch, 3),
            "probes": len(offsets),
            "first_offset_ms": offsets[0],
            "last_offset_ms": offsets[-1],
            "interval_ms": self.interval_ms,
            "min_interval_ms": self.min_interval_ms,
            "window_span_ms": round(offsets[-1] - offsets[0], 3),
        }


def replay(probes: Iterable[tuple[float, str]]) -> list[WindowProbe]:
    """Build probes from ``(epoch, phase_name)`` pairs, e.g. a saved run log."""
    built: list[WindowProbe] = []
    for epoch, name in probes:
        try:
            phase = WindowPhase(name)
        except ValueError as exc:
            raise ProbeError(f"未知的窗口状态：{name}") from exc
        built.append(WindowProbe(at_local=float(epoch), phase=phase))
    return built
