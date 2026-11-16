"""Sub-second school-clock offset from HTTP ``Date`` rollover.

The gap this closes
-------------------
``Date`` has one-second resolution: a single sample only places the server
clock inside ``[D, D+1)``, i.e. ±500 ms plus half the round trip.  The window
bracket that :mod:`core.window_probe` measures is only useful if the offset
feeding ``server_flip_epoch`` is tighter than the bracket itself — otherwise
estimating a sub-second flip just moves the error from the bracket into the
offset.

:class:`core.time_sync.TimeSync` deliberately treats ``Date`` as a coarse
fallback ("秒级粗校时") and prefers NTP.  That is the right call for *UTC*, and
the wrong one for a seat race: the gate that decides it runs on the school
host, so the school's own ``Date`` header is the only signal that reflects that
clock.  NTP tells you the real time; it does not tell you whether the school's
server agrees with it.

The trick
---------
``Date`` advances from ``T`` to ``T+1`` exactly when the server clock crosses an
integer second.  Two samples that straddle that crossing bracket the instant:

* ``a``: ``Date = T``     — processed before the crossing, and not before ``a``
  was sent, so the crossing is *after* ``a.sent_epoch``;
* ``b``: ``Date = T+1``   — processed at or after the crossing, and not after
  ``b`` was received, so the crossing is *before* ``b.received_epoch``.

    T0 ∈ [max(sent of Date=T), min(received of Date=T+1)]

The width of that interval is the sampling gap, not the one-second resolution of
``Date``.  Sampling every 18 ms brackets T0 to 18 ms; bisecting the bracket five
more times lands inside ~6 ms.  No symmetry assumption is made about the paths,
which is why the bound is the send/receive walls rather than the narrower
midpoint pair.

What this module does *not* do
------------------------------
It issues no requests.  Callers own their budget and their sampler, exactly as
in :mod:`core.window_probe`.  :func:`estimate_offset` is arithmetic over samples
already taken; :func:`next_probe_after` only returns a suggested instant.
Nothing here can turn a compliant client into a burst client by accident.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

#: Intrinsic half-width of a single ``Date`` observation, in milliseconds.
DATE_RESOLUTION_MS = 1000.0

#: Two samples closer than this describe the same instant and cannot tighten
#: the bracket further.
MIN_MEANINGFUL_GAP_MS = 1.0

#: Upper bound on samples consumed by one estimate, so a runaway log cannot make
#: the arithmetic unbounded.
MAX_SAMPLES = 500

#: Plausible epoch range for a sample (2001-09-09 .. 2100-01-01).
_MIN_EPOCH = 1_000_000_000.0
_MAX_EPOCH = 4_102_444_800.0


class DateSyncError(ValueError):
    """The sample set does not satisfy the estimation preconditions."""


@dataclass(frozen=True)
class DateSample:
    """One completed ``Date`` observation, all instants in local epoch seconds.

    ``server_epoch`` is the integer second parsed out of the response header.
    ``age_seconds`` must stay ``0``: a cached ``Date`` describes whatever proxy
    answered, not the school host, and silently using it would inject an
    arbitrary error into the estimate.
    """

    sent_epoch: float
    received_epoch: float
    server_epoch: int
    age_seconds: int = 0

    def __post_init__(self) -> None:
        for name in ("sent_epoch", "received_epoch"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise DateSyncError(f"{name} 必须是数值")
            if not _MIN_EPOCH <= float(value) <= _MAX_EPOCH:
                raise DateSyncError(f"{name} 超出合理区间: {value!r}")
        if self.received_epoch < self.sent_epoch:
            raise DateSyncError(
                f"接收时刻早于发送时刻: {self.sent_epoch} -> {self.received_epoch}"
            )
        if not isinstance(self.server_epoch, int) or isinstance(self.server_epoch, bool):
            raise DateSyncError("server_epoch 必须是整秒")
        if self.server_epoch != int(self.server_epoch):
            raise DateSyncError("server_epoch 必须是整秒")
        if not _MIN_EPOCH <= self.server_epoch <= _MAX_EPOCH:
            raise DateSyncError(f"server_epoch 超出合理区间: {self.server_epoch}")
        if self.age_seconds:
            raise DateSyncError(
                "Age>0 的 Date 来自缓存代理，不能用于校时"
            )

    @property
    def round_trip_ms(self) -> float:
        return (self.received_epoch - self.sent_epoch) * 1000.0

    @property
    def midpoint_epoch(self) -> float:
        return (self.sent_epoch + self.received_epoch) / 2.0


@dataclass(frozen=True)
class ClockEstimate:
    """Offset of the school clock relative to this machine.

    ``offset_ms > 0`` means the school clock is *ahead* of local time, matching
    :attr:`core.time_sync.TimeSync.offset_ms`.
    """

    offset_ms: float | None
    uncertainty_ms: float
    crossed: bool
    samples_used: int
    last_closed: DateSample | None = None
    first_open: DateSample | None = None
    span_seconds: int = 1
    detail: str = ""

    @property
    def is_sub_second(self) -> bool:
        return self.crossed and self.uncertainty_ms < DATE_RESOLUTION_MS


def _check(samples: Sequence[DateSample]) -> list[DateSample]:
    if not samples:
        raise DateSyncError("至少需要一个采样")
    if len(samples) > MAX_SAMPLES:
        raise DateSyncError(f"采样数 {len(samples)} 超过上限 {MAX_SAMPLES}")
    for sample in samples:
        if not isinstance(sample, DateSample):
            raise DateSyncError(f"非 DateSample 项: {type(sample).__name__}")
    ordered = sorted(samples, key=lambda s: s.received_epoch)
    seen: set[float] = set()
    for sample in ordered:
        if sample.received_epoch in seen:
            raise DateSyncError(
                f"重复的接收时刻 {sample.received_epoch}；同一瞬间的两次观察"
                "无法判断先后，估计拒绝猜测"
            )
        seen.add(sample.received_epoch)
    return ordered


def estimate_offset(samples: Iterable[DateSample]) -> ClockEstimate:
    """Bracket the school clock offset from a burst of ``Date`` samples.

    The returned ``offset_ms`` is the midpoint of the bracket and
    ``uncertainty_ms`` its half-width — the same convention
    :class:`core.time_sync.TimeSync` uses, so the two can be compared directly.
    """
    ordered = _check(list(samples))

    base_second = ordered[0].server_epoch
    split = None
    for index, sample in enumerate(ordered):
        if sample.server_epoch != base_second:
            split = index
            break

    # ---- 未跨越：只能退回单次最优估计，仍是秒级 ----
    if split is None:
        best = min(ordered, key=lambda s: s.round_trip_ms)
        offset = best.server_epoch + 0.5 - best.midpoint_epoch
        error = DATE_RESOLUTION_MS / 2 + best.round_trip_ms / 2
        return ClockEstimate(
            offset_ms=offset * 1000.0,
            uncertainty_ms=error,
            crossed=False,
            samples_used=len(ordered),
            detail=(
                f"{len(ordered)} 个采样全部落在同一秒内，未观察到 Date 翻转；"
                "退回单次估计，误差仍在半秒量级。需要跨越整秒边界的采样对。"
            ),
        )

    closed = ordered[:split]
    opened = ordered[split:]
    d_closed = closed[-1].server_epoch
    d_open = opened[0].server_epoch

    if d_open < d_closed:
        raise DateSyncError(
            f"Date 逆行: {d_closed} -> {d_open}；服务器时钟回拨或采样源不一致"
        )

    lower = max(s.sent_epoch for s in closed)      # T0 严格晚于它
    upper = min(s.received_epoch for s in opened)  # T0 不晚于它

    if upper <= lower:
        raise DateSyncError(
            "跨秒采样的发送/接收窗口互不重叠，采样集自相矛盾："
            f"T0 下界 {lower} 不早于上界 {upper}"
        )

    span = upper - lower
    if span * 1000.0 < MIN_MEANINGFUL_GAP_MS:
        raise DateSyncError(
            f"跨秒区间 {span * 1000.0:.3f} ms 小于最小有意义间隔 "
            f"{MIN_MEANINGFUL_GAP_MS} ms"
        )

    boundary = d_closed + 1                     # 服务器时钟跨过的整秒
    offset_low = boundary - upper
    offset_high = boundary - lower
    offset_ms = (offset_low + offset_high) / 2 * 1000.0
    uncertainty_ms = (offset_high - offset_low) / 2 * 1000.0

    span_seconds = d_open - d_closed
    detail = (
        f"跨秒对: 关闭侧最新发送 {lower:.3f}，开放侧最早接收 {upper:.3f}，"
        f"区间 {span * 1000.0:.1f} ms"
    )
    if span_seconds != 1:
        detail += f"；Date 一次跳过 {span_seconds} 秒，采样间隙过大，精度受限"

    return ClockEstimate(
        offset_ms=offset_ms,
        uncertainty_ms=uncertainty_ms,
        crossed=True,
        samples_used=len(ordered),
        last_closed=closed[-1],
        first_open=opened[0],
        span_seconds=span_seconds,
        detail=detail,
    )


def next_probe_after(
    estimate: ClockEstimate,
    *,
    target_ms: float = 10.0,
    min_gap_ms: float = MIN_MEANINGFUL_GAP_MS,
) -> float | None:
    """Suggest the local instant for one more sample that would tighten the bracket.

    Returns ``None`` when the bracket is already at or below ``target_ms``, or
    when no bracket exists to narrow.  The caller is still responsible for its
    own rate budget: this only says *when*, never *whether* it is permitted.
    """
    if not estimate.crossed:
        return None
    if not isinstance(target_ms, (int, float)) or target_ms <= 0:
        raise DateSyncError("target_ms 必须为正数")
    if estimate.uncertainty_ms <= target_ms:
        return None

    last = estimate.last_closed
    first = estimate.first_open
    if last is None or first is None:
        return None

    lower = last.sent_epoch
    upper = first.received_epoch
    middle = (lower + upper) / 2.0

    gap_s = max(min_gap_ms, 0.0) / 1000.0
    if upper - middle < gap_s:
        middle = upper - gap_s
    if middle - lower < gap_s:
        return None
    return middle


def bisect_plan(
    estimate: ClockEstimate,
    *,
    target_ms: float = 6.0,
    max_rounds: int = 5,
) -> tuple[float, ...]:
    """Pre-compute the sampling instants a bisection would use.

    Purely a schedule of suggestions: nothing is sampled here, and the width of
    the returned tuple is bounded by ``max_rounds`` so a caller cannot be tricked
    into an unbounded burst by passing a tiny ``target_ms``.
    """
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 0:
        raise DateSyncError("max_rounds 必须是非负整数")
    plan: list[float] = []
    current = estimate
    for _ in range(max_rounds):
        instant = next_probe_after(current, target_ms=target_ms)
        if instant is None:
            break
        plan.append(instant)
        # 假设打一枪就把区间对半（保守写死，不做乐观折扣）
        current = ClockEstimate(
            offset_ms=current.offset_ms,
            uncertainty_ms=current.uncertainty_ms / 2.0,
            crossed=True,
            samples_used=current.samples_used + 1,
            last_closed=current.last_closed,
            first_open=current.first_open,
            span_seconds=current.span_seconds,
            detail=current.detail,
        )
    return tuple(plan)
