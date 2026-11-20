"""Selection strategy layer: clock alignment, prewarm, release detection,
failure classification, fallback ordering and peak-window polling.

Ported from ``tools/kingo-live/optimizations.py`` so the Windows client and the
research CLI share ONE implementation. ``tools/kingo-live/optimizations.py`` is
now a thin re-export shim; edit this file, not that one.

Design constraints taken from ``tools/bench`` measurements:

* Discovery latency dominates. Baseline polling discovers a release at ~28 s,
  correct polling at ~281 ms, while submit stays ~20 ms. Optimise detection.
* Concurrency is negative value: 8 concurrent submits dropped success from
  1.00 to 0.125 and raised total p95 from 70 ms to 1178 ms. Everything here is
  therefore serial by construction.
* Unknown outcomes halt. A blind retry after an unverified write is the one
  mistake that creates duplicate enrollments.

No network access happens in this module: every function consumes data the
caller already fetched, or returns a value the caller must act on.
"""
from __future__ import annotations

import datetime as _dt
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

__all__ = [
    "ClockSample", "ClockSync", "Decision", "FailoverPlanner",
    "FailureClassifier", "PeakWindow", "PollingSchedule", "Prewarmer",
    "ReleaseEvent", "ReleaseMonitor", "RetryBackoff", "WarmResult",
    "offering_fingerprint", "parse_server_time",
]

#: Markers that mean the school wants a human challenge (image CAPTCHA, slider,
#: SMS code, CAS re-authentication). Hitting one is a *safety* stop, not a
#: transient error: retrying turns a solvable challenge into an account lock.
DEFAULT_CHALLENGE_MARKERS = (
    "验证码", "请输入验证码", "校验码", "滑块", "拖动滑块", "点击验证",
    "短信验证", "手机验证", "动态码", "二次验证", "统一身份认证",
    "请重新登录", "会话已失效", "captcha", "slider", "verify code",
    "verification code", "sms code", "one-time password", "otp",
    "re-enter your password", "session expired", "ticket expired",
)

#: Absolute floor for any polling interval. Mirrors monitor_policy; duplicated
#: here so this module never imports a policy module just for a constant.
ABSOLUTE_MIN_INTERVAL = 30.0


# ---------------------------------------------------------------------------
# P1: server clock alignment
# ---------------------------------------------------------------------------

def parse_server_time(value: str, header: str) -> Optional[float]:
    """Return a real epoch timestamp from an HTTP response header.

    Date: RFC 7231 IMF-fixdate, resolution = 1 s.
    Custom header: epoch milliseconds (>1e11), epoch seconds, or ISO-8601.
    """
    text = (value or "").strip()
    if not text:
        return None
    if header.lower() != "date":
        if text.replace(".", "", 1).isdigit():
            numeric = float(text)
            return numeric / 1000.0 if numeric > 1e11 else numeric
        try:
            moment = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=_dt.timezone.utc)
            return moment.timestamp()
        except ValueError:
            pass
    try:
        moment = parsedate_to_datetime(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=_dt.timezone.utc)
        return moment.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class ClockSample:
    header_value: str
    rtt_ms: float
    offset_ms: float
    precision_ms: float
    local_midpoint: float
    server_epoch: float


@dataclass
class ClockSync:
    """Multi-sample, median-based estimate of (server_time - local_time).

    offset_ms = (server_epoch - local_midpoint) * 1000
    local_midpoint = (sent_at + received_at) / 2   # RTT/2 compensation

    ``Date`` has 1 s resolution, so a Date-only sync cannot place T0 within a
    second. Prefer a millisecond header when the server exposes one, otherwise
    use window-flip calibration and treat ``precision_ms`` as unknown.
    """

    header: str = "Date"
    samples_target: int = 5
    samples: List[ClockSample] = field(default_factory=list)
    offset_ms: float = 0.0
    precision_ms: float = 1000.0

    @property
    def synced(self) -> bool:
        return bool(self.samples)

    @property
    def spread_ms(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        values = [s.offset_ms for s in self.samples]
        return round(max(values) - min(values), 3)

    def observe(self, resp: Any, sent_at: float, received_at: float) -> Optional[ClockSample]:
        raw = resp.headers.get(self.header.lower(), "")
        server_epoch = parse_server_time(raw, self.header)
        if server_epoch is None:
            return None
        midpoint = (sent_at + received_at) / 2.0
        precision = 1000.0 if self.header.lower() == "date" else 1.0
        sample = ClockSample(
            header_value=raw,
            rtt_ms=round((received_at - sent_at) * 1000.0, 3),
            offset_ms=round((server_epoch - midpoint) * 1000.0, 3),
            precision_ms=precision,
            local_midpoint=midpoint,
            server_epoch=server_epoch)
        self.samples.append(sample)
        self.precision_ms = precision
        self.offset_ms = round(statistics.median(x.offset_ms for x in self.samples), 3)
        return sample

    def now_server(self) -> float:
        return time.time() + self.offset_ms / 1000.0

    def wait_until(self, target_epoch: float, lead_ms: float = 50.0,
                   max_wait_s: float = 600.0) -> dict:
        """Block until estimated server clock reaches target_epoch - lead_ms.

        Sleeps in 1 s slices, then busy-waits the last 20 ms. Returns a real
        measurement of the wait and the alignment error at return time.
        """
        started = time.perf_counter()
        deadline = started + max_wait_s
        while True:
            remain_ms = (target_epoch - self.now_server()) * 1000.0 - lead_ms
            if remain_ms <= 0.0:
                return {
                    "aligned": True,
                    "waited_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "lead_ms": lead_ms,
                    "offset_ms": self.offset_ms,
                    "precision_ms": self.precision_ms,
                    "error_ms": round(-remain_ms, 3),
                }
            if time.perf_counter() > deadline:
                return {
                    "aligned": False,
                    "waited_ms": round((time.perf_counter() - started) * 1000.0, 3),
                    "lead_ms": lead_ms,
                    "offset_ms": self.offset_ms,
                    "precision_ms": self.precision_ms,
                    "error_ms": round(remain_ms, 3),
                    "reason": "max_wait_exceeded",
                }
            if remain_ms > 20.0:
                time.sleep(min(remain_ms / 1000.0, 1.0))
            else:
                spin_until = time.perf_counter() + remain_ms / 1000.0
                while time.perf_counter() < spin_until:
                    pass

    def report(self) -> dict:
        return {
            "synced": self.synced,
            "header": self.header,
            "samples": len(self.samples),
            "offset_ms": self.offset_ms,
            "precision_ms": self.precision_ms,
            "spread_ms": self.spread_ms,
            "now_server_epoch": round(self.now_server(), 6),
            "sample_offsets_ms": [s.offset_ms for s in self.samples],
            "sample_rtt_ms": [s.rtt_ms for s in self.samples],
        }


# ---------------------------------------------------------------------------
# P1: session + connection prewarming
# ---------------------------------------------------------------------------

@dataclass
class WarmResult:
    transport: int
    status: int
    auth_ok: bool
    handshake_ms: float
    reused: bool
    error: str = ""


class Prewarmer:
    """Real login-state probe and real connection establishment before T0.

    Duck-types the research engine (``engine.transports`` + ``engine.budgeted``)
    so the same class serves both the CLI and, once a native transport pool
    exists, the Windows client.
    """

    def __init__(self, cfg: dict):
        warm = cfg.get("warmup", {})
        self.path = warm.get("path", cfg.get("catalog", {}).get("path", "/"))
        self.method = warm.get("method", "GET")
        self.ok_status = set(warm.get("auth_ok_status", [200]))
        self.login_markers = [m.lower() for m in warm.get(
            "login_markers", ["登录", "请先登录", "login", "session expired"])]
        self.probes = max(1, int(warm.get("probes", 2)))
        self.keep_alive = bool(warm.get("keep_alive", False))

    def probe_session(self, resp: Any) -> bool:
        if resp.status not in self.ok_status or resp.error:
            return False
        text = resp.body.decode("utf-8", errors="replace").lower()
        return not any(marker in text for marker in self.login_markers)

    def warm(self, engine: Any) -> dict:
        results: List[WarmResult] = []
        for index, transport in enumerate(engine.transports):
            first: Any = None
            last_reused = False
            handshake = 0.0
            error = ""
            for attempt in range(self.probes):
                response = engine.budgeted(transport, self.method, self.path)
                if attempt == 0:
                    first = response
                    handshake = response.connect_ms
                last_reused = bool(getattr(response, "reused", False))
                if response.error:
                    error = response.error
                    break
            ok = first is not None and self.probe_session(first)
            results.append(WarmResult(
                transport=index, status=first.status if first else 0, auth_ok=ok,
                handshake_ms=round(handshake, 3), reused=last_reused, error=error))
        return {
            "probes_per_transport": self.probes,
            "keep_alive": self.keep_alive,
            "sessions": len(results),
            "all_auth_ok": all(item.auth_ok for item in results),
            "handshake_p50_ms": round(statistics.median(
                [item.handshake_ms for item in results]), 3) if results else 0.0,
            "connection_reused": any(item.reused for item in results),
            "results": [asdict(item) for item in results],
        }


# ---------------------------------------------------------------------------
# P2: seat-release event detection over catalog snapshots
# ---------------------------------------------------------------------------

def offering_fingerprint(offering: Any) -> str:
    """Stable identity for an offering across the research and client models."""
    method = getattr(offering, "fingerprint", None)
    if callable(method):
        return str(method())
    course = getattr(offering, "course_id", "")
    klass = getattr(offering, "class_id", "")
    return f"{course}\x1f{klass}"


@dataclass
class ReleaseEvent:
    kind: str                 # new | reopened | increased
    course_id: str
    class_id: str
    remain: Optional[int]
    previous_remain: Optional[int]
    at_local: float
    poll_cost_ms: float

    def as_dict(self) -> dict:
        return asdict(self)


class ReleaseMonitor:
    """Diff consecutive REAL catalog reads. Empty polls produce no events.

    Tracking the whole catalog rather than only configured targets is what makes
    this useful for pickup: a seat that frees up in an unfollowed teaching class
    is still a seat, and the caller can decide whether to widen the plan.

    ``max_events`` bounds the in-memory history. An unattended run polls for
    hours; an unbounded list is a slow memory leak in an EXE that is supposed to
    survive a whole release window. Oldest events are dropped, newest kept.
    """

    def __init__(self, *, max_events: int = 2000) -> None:
        if type(max_events) is not int or not 16 <= max_events <= 100_000:
            raise ValueError("事件上限必须在 16–100000 之间")
        self.max_events = max_events
        self.snapshot: Dict[str, Any] = {}
        self.polls = 0
        self.events: List[ReleaseEvent] = []
        self.dropped_events = 0

    def observe(self, offers: List[Any], poll_cost_ms: float = 0.0) -> List[ReleaseEvent]:
        self.polls += 1
        now = time.time()
        current = {offering_fingerprint(o): o for o in offers}
        events: List[ReleaseEvent] = []
        for fingerprint, offering in current.items():
            current_remain = getattr(offering, "remain", None) or 0
            previous = self.snapshot.get(fingerprint)
            if previous is None:
                if current_remain > 0:
                    events.append(ReleaseEvent(
                        "new", getattr(offering, "course_id", ""),
                        getattr(offering, "class_id", ""), getattr(offering, "remain", None),
                        None, now, poll_cost_ms))
                continue
            previous_remain_raw = getattr(previous, "remain", None)
            previous_remain = previous_remain_raw or 0
            if previous_remain <= 0 < current_remain:
                kind = "reopened"
            elif current_remain > previous_remain:
                kind = "increased"
            else:
                continue
            events.append(ReleaseEvent(
                kind, getattr(offering, "course_id", ""),
                getattr(offering, "class_id", ""), getattr(offering, "remain", None),
                previous_remain_raw, now, poll_cost_ms))
        self.snapshot = current
        self.events.extend(events)
        if len(self.events) > self.max_events:
            overflow = len(self.events) - self.max_events
            del self.events[:overflow]
            self.dropped_events += overflow
        return events

    def available_now(self, *, min_remain: int = 1) -> List[Any]:
        """Offerings with a real seat in the most recent snapshot.

        ``remain is None`` is UNKNOWN and never counts as available. Sorted by
        remaining seats descending so the caller sees the safest target first.
        """
        if type(min_remain) is not int or min_remain < 1:
            raise ValueError("最小余量必须是正整数")
        rows = [o for o in self.snapshot.values()
                if isinstance(getattr(o, "remain", None), int)
                and getattr(o, "remain") >= min_remain]
        return sorted(rows, key=lambda o: (-int(getattr(o, "remain") or 0),
                                           str(getattr(o, "course_id", "")),
                                           str(getattr(o, "class_id", ""))))

    def widen_suggestions(self, course_id: str, *,
                          exclude_class_ids: Optional[List[str]] = None,
                          min_remain: int = 1,
                          limit: int = 5) -> List[dict]:
        """Same-course alternatives with seats, most remaining first.

        Use when the configured class is full: the plan can be widened without
        re-reading the catalog, and without ever mixing two courses into one
        submission attempt.
        """
        if not isinstance(course_id, str) or not course_id.strip():
            raise ValueError("缺少课程代码")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("建议条数必须在 1–50 之间")
        excluded = {str(value).strip() for value in (exclude_class_ids or []) if value}
        rows = []
        for offering in self.available_now(min_remain=min_remain):
            if str(getattr(offering, "course_id", "")) != course_id:
                continue
            class_id = str(getattr(offering, "class_id", "") or "")
            if class_id and class_id in excluded:
                continue
            rows.append({
                "course_id": course_id,
                "class_id": class_id,
                "remain": getattr(offering, "remain", None),
                "course_name": str(getattr(offering, "course_name", "") or ""),
                "teacher": str(getattr(offering, "teacher", "") or ""),
            })
            if len(rows) >= limit:
                break
        return rows

    def report(self) -> dict:
        return {
            "polls": self.polls,
            "tracked_offerings": len(self.snapshot),
            "events": len(self.events),
            "dropped_events": self.dropped_events,
            "max_events": self.max_events,
            "kinds": {kind: sum(1 for e in self.events if e.kind == kind)
                      for kind in ("new", "reopened", "increased")},
        }


# ---------------------------------------------------------------------------
# P2: peak-window polling (pickup strategy)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PeakWindow:
    """A daily local-time window during which polling tightens to the floor.

    Withdrawal peaks in a Kingosoft term are reproducible: shortly after the
    window opens (wrong picks get dropped), the two hours before the deadline,
    and the first morning of the supplementary round.
    """

    weekday: Optional[int]      # 0=Monday .. 6=Sunday; None = every day
    start: str                  # "HH:MM"
    end: str                    # "HH:MM", may wrap past midnight
    label: str = ""

    def __post_init__(self) -> None:
        if self.weekday is not None and (type(self.weekday) is not int
                                         or not 0 <= self.weekday <= 6):
            raise ValueError("高峰窗口星期无效")
        for name in ("start", "end"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                raise ValueError("高峰窗口时间必须为 HH:MM")
            hour, minute = value[:2], value[3:]
            if not (hour.isdigit() and minute.isdigit()):
                raise ValueError("高峰窗口时间必须为 HH:MM")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError("高峰窗口时间越界")

    def _minutes(self, value: str) -> int:
        return int(value[:2]) * 60 + int(value[3:])

    def contains(self, moment: _dt.datetime) -> bool:
        if self.weekday is not None and moment.weekday() != self.weekday:
            return False
        current = moment.hour * 60 + moment.minute
        start, end = self._minutes(self.start), self._minutes(self.end)
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end     # wraps midnight

    def as_dict(self) -> dict:
        return {"weekday": self.weekday, "start": self.start, "end": self.end,
                "label": self.label}


class PollingSchedule:
    """Choose a poll interval from peak windows, never below the hard floor.

    ``floor`` is 30 s because that is the client-wide absolute minimum; a peak
    window may not go below it, and a school policy that demands more may not be
    overridden. This class only ever slows polling down or tightens it to the
    floor; it cannot exceed what the operator approved.
    """

    def __init__(self, *, peak_interval: float = ABSOLUTE_MIN_INTERVAL,
                 offpeak_interval: float = 60.0,
                 windows: Optional[List[PeakWindow]] = None) -> None:
        for value, label in ((peak_interval, "高峰间隔"), (offpeak_interval, "平时间隔")):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{label}无效")
            if value < ABSOLUTE_MIN_INTERVAL or value > 3600:
                raise ValueError(f"{label}必须在 {ABSOLUTE_MIN_INTERVAL:.0f}–3600 秒之间")
        if peak_interval > offpeak_interval:
            raise ValueError("高峰间隔不应大于平时间隔")
        self.peak_interval = float(peak_interval)
        self.offpeak_interval = float(offpeak_interval)
        self.windows = list(windows or [])

    @classmethod
    def from_config(cls, strategy: dict) -> "PollingSchedule":
        """Read ``strategy.pickup``; absent or disabled keeps current behaviour."""
        pickup = (strategy or {}).get("pickup") or {}
        if not isinstance(pickup, dict) or pickup.get("enabled") is not True:
            low, high = (strategy or {}).get("interval_range", [60, 60])
            return cls(peak_interval=max(ABSOLUTE_MIN_INTERVAL, float(low)),
                       offpeak_interval=max(ABSOLUTE_MIN_INTERVAL, float(high)),
                       windows=[])
        windows = []
        for raw in pickup.get("peak_windows", []):
            if not isinstance(raw, dict):
                raise ValueError("高峰窗口配置格式无效")
            windows.append(PeakWindow(
                weekday=raw.get("weekday"), start=str(raw.get("start", "")),
                end=str(raw.get("end", "")), label=str(raw.get("label", ""))))
        return cls(peak_interval=float(pickup.get("peak_interval_seconds",
                                                  ABSOLUTE_MIN_INTERVAL)),
                   offpeak_interval=float(pickup.get("offpeak_interval_seconds", 60)),
                   windows=windows)

    def active_window(self, moment: Optional[_dt.datetime] = None) -> Optional[PeakWindow]:
        current = moment or _dt.datetime.now()
        for window in self.windows:
            if window.contains(current):
                return window
        return None

    def interval(self, moment: Optional[_dt.datetime] = None) -> float:
        return (self.peak_interval if self.active_window(moment) is not None
                else self.offpeak_interval)

    def to_config(self) -> dict:
        """Round-trip this schedule back into ``strategy.pickup`` form.

        ``tools/patch/p1_polling_plan.py`` writes this shape; the client reads it
        back through ``from_config``. Keeping the writer and the reader in one
        module is what stops the two from drifting apart.
        """
        return {
            "enabled": bool(self.windows),
            "peak_interval_seconds": self.peak_interval,
            "offpeak_interval_seconds": self.offpeak_interval,
            "peak_windows": [window.as_dict() for window in self.windows],
        }

    def report(self, moment: Optional[_dt.datetime] = None) -> dict:
        active = self.active_window(moment)
        return {"peak_interval_s": self.peak_interval,
                "offpeak_interval_s": self.offpeak_interval,
                "floor_s": ABSOLUTE_MIN_INTERVAL,
                "active_window": active.as_dict() if active else None,
                "effective_interval_s": self.interval(moment),
                "windows": [w.as_dict() for w in self.windows]}


# ---------------------------------------------------------------------------
# P4/P6: failure classification -> explicit action, never a blind retry
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    """One classified outcome plus the ONLY action permitted next.

    ``delay_s`` is the caller's minimum wait before the next attempt of the same
    kind. It is advisory: 0.0 means "no timing constraint from this module".
    """

    kind: str
    action: str        # stop | halt | next | cooldown | backoff | wait
    detail: str = ""
    delay_s: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


class RetryBackoff:
    """Bounded exponential backoff with jitter for *idempotent reads only*.

    A 429 or a 5xx may be retried because a rejected read changed nothing. A
    time-out on a write is NOT retried here by design: an unknown write outcome
    belongs to the journal/recovery path, never to a timer.

    ``jitter`` is injectable so tests are deterministic; the default uses
    ``random.random`` and therefore never emits a fixed interval that a remote
    pattern detector could key on.
    """

    def __init__(self, *, base_s: float = 2.0, cap_s: float = 300.0,
                 factor: float = 2.0, jitter_ratio: float = 0.25,
                 jitter: Optional[Any] = None, max_attempts: int = 6) -> None:
        for value, label, upper in ((base_s, "退避基数", 600.0),
                                    (cap_s, "退避上限", 3600.0)):
            if (type(value) not in (int, float) or isinstance(value, bool)
                    or not 0 < float(value) <= upper):
                raise ValueError(f"{label}必须是 (0, {upper:g}] 内的数字")
        if type(factor) not in (int, float) or not 1.0 <= float(factor) <= 8.0:
            raise ValueError("退避倍数必须在 1–8 之间")
        if type(jitter_ratio) not in (int, float) or not 0.0 <= float(jitter_ratio) <= 1.0:
            raise ValueError("抖动比例必须在 0–1 之间")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("最大重试次数必须在 1–20 之间")
        if float(base_s) > float(cap_s):
            raise ValueError("退避基数不应大于上限")
        self.base_s = float(base_s)
        self.cap_s = float(cap_s)
        self.factor = float(factor)
        self.jitter_ratio = float(jitter_ratio)
        self.max_attempts = max_attempts
        self._jitter = jitter if callable(jitter) else _default_jitter
        self.attempts = 0
        self.last_delay_s = 0.0

    def next_delay(self) -> Optional[float]:
        """Next wait, or ``None`` when the attempt budget is exhausted."""
        if self.attempts >= self.max_attempts:
            return None
        raw = min(self.cap_s, self.base_s * (self.factor ** self.attempts))
        self.attempts += 1
        spread = raw * self.jitter_ratio
        # Symmetric jitter around the raw delay, never below half the raw value
        # so a 429 cannot be replayed immediately.
        delay = max(raw * 0.5, raw + (self._jitter() * 2.0 - 1.0) * spread)
        delay = min(self.cap_s, delay)
        self.last_delay_s = delay
        return delay

    def reset(self) -> None:
        self.attempts = 0
        self.last_delay_s = 0.0

    def report(self) -> dict:
        return {"attempts": self.attempts, "last_delay_s": round(self.last_delay_s, 3),
                "max_attempts": self.max_attempts, "cap_s": self.cap_s}


def _default_jitter() -> float:
    import random
    return random.random()


#: Statuses whose retry is safe because the server definitively did not act.
RETRYABLE_READ_STATUSES = (429, 500, 502, 503, 504)


class FailureClassifier:
    """One real outcome -> one action.

    action semantics:
      stop      confirmed by official readback, whole chain finished
      halt      unknown/pending result or human challenge: stop and reconcile
      next      this target is dead, move to the next fallback (no retry)
      cooldown  honour Retry-After
      backoff   transient overload
      wait      selection window not open yet

    A human challenge is never classified as a transient error. ``halt`` keeps
    the workflow offline so the operator can solve it manually; treating it as
    ``backoff`` would burn attempts against a counter that locks the account.
    """

    def __init__(self, cfg: dict, *, retry: Optional[RetryBackoff] = None):
        business = cfg.get("business", {})
        self.success_field = business.get("success_field", "")
        self.success_values = set(str(v).lower() for v in business.get(
            "success_values", [True, 1, "1", "true", "success", "ok"]))
        self.full_markers = [m.lower() for m in business.get(
            "full_markers", ["已满", "名额已满", "无余量", "no seat", "full"])]
        self.window_markers = [m.lower() for m in business.get(
            "window_markers", ["不是有效的选课时间", "未开放", "尚未开始",
                               "not in selection time"])]
        self.challenge_markers = [m.lower() for m in business.get(
            "challenge_markers", DEFAULT_CHALLENGE_MARKERS)]
        self.retry = retry

    def classify(self, status: int, body: bytes, readback_ok: bool,
                 network_error: str = "") -> Decision:
        text = body.decode("utf-8", errors="replace").lower() if body else ""
        if network_error or status == 0:
            return Decision("unknown", "halt", network_error or "no_response")
        # Check the challenge BEFORE the status code: a challenge is usually a
        # 200 with a form in the body, and 403+CAPTCHA must not become "auth".
        if any(marker in text for marker in self.challenge_markers):
            # No delay and no ladder consumption: a challenge is not a retry,
            # it is a stop-and-let-the-operator-solve-it signal.
            return Decision("challenge_required", "halt", "human_challenge")
        if status in (401, 403):
            return Decision("auth", "halt", f"http={status}")
        if status == 429:
            return Decision("rate_limited", "cooldown", "http=429",
                            self._bump(0.0))
        if status in (500, 502, 503, 504):
            return Decision("overload", "backoff", f"http={status}",
                            self._bump(0.0))
        if any(marker in text for marker in self.window_markers):
            return Decision("window_closed", "wait", "business_marker")
        if any(marker in text for marker in self.full_markers):
            return Decision("business_full", "next", "business_marker")
        if self.success_field:
            try:
                payload = json.loads(text)
                value = payload.get(self.success_field) if isinstance(payload, dict) else None
                if value is not None and str(value).lower() not in self.success_values:
                    return Decision("business_rejected", "next",
                                    f"{self.success_field}={value}")
            except (ValueError, TypeError):
                pass
        if readback_ok:
            return Decision("confirmed", "stop", "official_readback_match")
        return Decision("pending", "halt", "readback_no_match")

    def note_success(self) -> None:
        """A confirmed read reset the backoff ladder."""
        if self.retry is not None:
            self.retry.reset()

    def _bump(self, fallback: float) -> float:
        if self.retry is None:
            return fallback
        delay = self.retry.next_delay()
        return fallback if delay is None else delay


# ---------------------------------------------------------------------------
# P2: exact target matching + ordered fallback chain
# ---------------------------------------------------------------------------

class FailoverPlanner:
    """Targets are tried in listed order. Exact course_id + class_id only."""

    def __init__(self, targets: List[dict]):
        self.targets = [dict(t) for t in targets if t.get("course_id")]
        for index, target in enumerate(self.targets):
            target.setdefault("priority", index + 1)
            target.setdefault("class_id", "")
            target.setdefault("enabled", True)
        self.targets = sorted(
            [t for t in self.targets if t["enabled"]],
            key=lambda t: (int(t["priority"]), t["course_id"], t["class_id"]))
        self.cursor = 0
        self.history: List[dict] = []

    def __len__(self) -> int:
        return len(self.targets)

    def first(self) -> Optional[dict]:
        return self.targets[0] if self.targets else None

    def next_after(self, target: dict) -> Optional[dict]:
        for index, candidate in enumerate(self.targets):
            if (candidate["course_id"] == target["course_id"]
                    and candidate["class_id"] == target.get("class_id", "")):
                return self.targets[index + 1] if index + 1 < len(self.targets) else None
        return None

    @staticmethod
    def match(offers: List[Any], target: dict) -> Optional[Any]:
        want_class = str(target.get("class_id") or "").strip()
        for offering in offers:
            if str(getattr(offering, "course_id", "")) != str(target["course_id"]):
                continue
            if want_class and str(getattr(offering, "class_id", "")) != want_class:
                continue
            return offering
        return None

    def note(self, target: dict, decision: Decision, attempt: Any) -> None:
        self.history.append({
            "course_id": target["course_id"],
            "class_id": target.get("class_id", ""),
            "kind": decision.kind,
            "action": decision.action,
            "detail": decision.detail,
            "status": getattr(attempt, "status", 0),
            "success": getattr(attempt, "success", False),
        })

    def report(self) -> dict:
        return {"targets": len(self.targets), "tried": len(self.history),
                "history": list(self.history)}
