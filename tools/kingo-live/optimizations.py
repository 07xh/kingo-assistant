#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
optimizations.py - the high-value layer of the real course-selection client.

Everything in this module runs on REAL HTTP responses produced by
live_client.Transport. There is no simulation, no synthetic clock, no fake
seat ledger.

Modules
-------
ClockSync        P1  Estimate server clock offset from real response headers.
                     RFC 7231 Date has 1-second resolution; a deployment may
                     expose a millisecond header (e.g. X-Server-Time) instead.
                     N samples, median offset, RTT/2 midpoint compensation.
Prewarmer        P1  Real login-state probe + DNS/TCP/TLS handshake + verified
                     persistent-connection reuse before the window opens.
ReleaseMonitor   P2  Snapshot diff of real catalog reads. Only a seat-release
                     event (new / reopened / increased) triggers a submission.
FailureClassifier P4/P6  Map a real HTTP + business + readback outcome to one
                     explicit action. Unknown results HALT, never auto-retry.
FailoverPlanner  P2  Exact course_id + class_id matching and an ordered
                     fallback chain. A failed target is not retried.

Python 3.10+ / standard library only.
"""

from __future__ import annotations

import datetime as _dt
import json
import statistics
import time
from dataclasses import dataclass, field, asdict
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # docs only - no runtime import, avoids a cycle
    from live_client import RawResponse, Offering  # noqa: F401


# ---------------------------------------------------------------------------
# P1: server clock alignment
# ---------------------------------------------------------------------------

def parse_server_time(value: str, header: str) -> Optional[float]:
    """Return a real epoch timestamp from an HTTP response header.

    Date: RFC 7231 IMF-fixdate, resolution = 1 s.
    Custom header: epoch milliseconds (>1e11), epoch seconds, or ISO-8601.
    """
    s = (value or "").strip()
    if not s:
        return None
    if header.lower() != "date":
        if s.replace(".", "", 1).isdigit():
            v = float(s)
            return v / 1000.0 if v > 1e11 else v
        try:
            d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return d.timestamp()
        except ValueError:
            pass
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
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
        v = [s.offset_ms for s in self.samples]
        return round(max(v) - min(v), 3)

    def observe(self, resp: Any, sent_at: float, received_at: float) -> Optional[ClockSample]:
        raw = resp.headers.get(self.header.lower(), "")
        server_epoch = parse_server_time(raw, self.header)
        if server_epoch is None:
            return None
        midpoint = (sent_at + received_at) / 2.0
        precision = 1000.0 if self.header.lower() == "date" else 1.0
        s = ClockSample(
            header_value=raw,
            rtt_ms=round((received_at - sent_at) * 1000.0, 3),
            offset_ms=round((server_epoch - midpoint) * 1000.0, 3),
            precision_ms=precision,
            local_midpoint=midpoint,
            server_epoch=server_epoch)
        self.samples.append(s)
        self.precision_ms = precision
        self.offset_ms = round(statistics.median(x.offset_ms for x in self.samples), 3)
        return s

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
    """Real login-state probe and real connection establishment before T0."""

    def __init__(self, cfg: dict):
        w = cfg.get("warmup", {})
        self.path = w.get("path", cfg.get("catalog", {}).get("path", "/"))
        self.method = w.get("method", "GET")
        self.ok_status = set(w.get("auth_ok_status", [200]))
        self.login_markers = [m.lower() for m in w.get(
            "login_markers", ["登录", "请先登录", "login", "session expired"])]
        self.probes = max(1, int(w.get("probes", 2)))
        self.keep_alive = bool(w.get("keep_alive", False))

    def probe_session(self, resp: Any) -> bool:
        if resp.status not in self.ok_status or resp.error:
            return False
        text = resp.body.decode("utf-8", errors="replace").lower()
        return not any(m in text for m in self.login_markers)

    def warm(self, engine: Any) -> dict:
        out: List[WarmResult] = []
        for i, tr in enumerate(engine.transports):
            first: Any = None
            last_reused = False
            handshake = 0.0
            err = ""
            for k in range(self.probes):
                r = engine.budgeted(tr, self.method, self.path)
                if k == 0:
                    first = r
                    handshake = r.connect_ms
                last_reused = bool(getattr(r, "reused", False))
                if r.error:
                    err = r.error
                    break
            ok = first is not None and self.probe_session(first)
            out.append(WarmResult(
                transport=i, status=first.status if first else 0, auth_ok=ok,
                handshake_ms=round(handshake, 3), reused=last_reused, error=err))
        return {
            "probes_per_transport": self.probes,
            "keep_alive": self.keep_alive,
            "sessions": len(out),
            "all_auth_ok": all(x.auth_ok for x in out),
            "handshake_p50_ms": round(statistics.median(
                [x.handshake_ms for x in out]), 3) if out else 0.0,
            "connection_reused": any(x.reused for x in out),
            "results": [asdict(x) for x in out],
        }


# ---------------------------------------------------------------------------
# P2: seat-release event detection over real catalog snapshots
# ---------------------------------------------------------------------------

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
    """Diff consecutive REAL catalog reads. Empty polls produce no events."""

    def __init__(self):
        self.snapshot: Dict[str, Any] = {}
        self.polls = 0
        self.events: List[ReleaseEvent] = []

    def observe(self, offers: List[Any], poll_cost_ms: float = 0.0) -> List[ReleaseEvent]:
        self.polls += 1
        now = time.time()
        current = {o.fingerprint(): o for o in offers}
        events: List[ReleaseEvent] = []
        for fp, o in current.items():
            cur = o.remain or 0
            prev = self.snapshot.get(fp)
            if prev is None:
                if cur > 0:
                    events.append(ReleaseEvent("new", o.course_id, o.class_id,
                                               o.remain, None, now, poll_cost_ms))
                continue
            prv = prev.remain or 0
            if prv <= 0 < cur:
                kind = "reopened"
            elif cur > prv:
                kind = "increased"
            else:
                continue
            events.append(ReleaseEvent(kind, o.course_id, o.class_id,
                                       o.remain, prev.remain, now, poll_cost_ms))
        self.snapshot = current
        self.events.extend(events)
        return events

    def report(self) -> dict:
        return {
            "polls": self.polls,
            "tracked_offerings": len(self.snapshot),
            "events": len(self.events),
            "kinds": {k: sum(1 for e in self.events if e.kind == k)
                      for k in ("new", "reopened", "increased")},
        }


# ---------------------------------------------------------------------------
# P4/P6: failure classification -> explicit action, never a blind retry
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    kind: str
    action: str        # stop | halt | next | cooldown | backoff | wait
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class FailureClassifier:
    """One real outcome -> one action.

    action semantics:
      stop      confirmed by official readback, whole chain finished
      halt      unknown/pending result: stop everything and reconcile
      next      this target is dead, move to the next fallback (no retry)
      cooldown  honour Retry-After
      backoff   transient overload
      wait      selection window not open yet
    """

    def __init__(self, cfg: dict):
        b = cfg.get("business", {})
        self.success_field = b.get("success_field", "")
        self.success_values = set(str(v).lower() for v in b.get(
            "success_values", [True, 1, "1", "true", "success", "ok"]))
        self.full_markers = [m.lower() for m in b.get(
            "full_markers", ["已满", "名额已满", "无余量", "no seat", "full"])]
        self.window_markers = [m.lower() for m in b.get(
            "window_markers", ["不是有效的选课时间", "未开放", "尚未开始",
                               "not in selection time"])]

    def classify(self, status: int, body: bytes, readback_ok: bool,
                 network_error: str = "") -> Decision:
        text = body.decode("utf-8", errors="replace").lower() if body else ""
        if network_error or status == 0:
            return Decision("unknown", "halt", network_error or "no_response")
        if status in (401, 403):
            return Decision("auth", "halt", f"http={status}")
        if status == 429:
            return Decision("rate_limited", "cooldown", "http=429")
        if status in (500, 502, 503, 504):
            return Decision("overload", "backoff", f"http={status}")
        if any(m in text for m in self.window_markers):
            return Decision("window_closed", "wait", "business_marker")
        if any(m in text for m in self.full_markers):
            return Decision("business_full", "next", "business_marker")
        if self.success_field:
            try:
                obj = json.loads(text)
                val = obj.get(self.success_field) if isinstance(obj, dict) else None
                if val is not None and str(val).lower() not in self.success_values:
                    return Decision("business_rejected", "next",
                                    f"{self.success_field}={val}")
            except (ValueError, TypeError):
                pass
        if readback_ok:
            return Decision("confirmed", "stop", "official_readback_match")
        return Decision("pending", "halt", "readback_no_match")


# ---------------------------------------------------------------------------
# P2: exact target matching + ordered fallback chain
# ---------------------------------------------------------------------------

class FailoverPlanner:
    """Targets are tried in listed order. Exact course_id + class_id only."""

    def __init__(self, targets: List[dict]):
        self.targets = [dict(t) for t in targets if t.get("course_id")]
        for i, t in enumerate(self.targets):
            t.setdefault("priority", i + 1)
            t.setdefault("class_id", "")
            t.setdefault("enabled", True)
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
        for i, t in enumerate(self.targets):
            if (t["course_id"] == target["course_id"]
                    and t["class_id"] == target.get("class_id", "")):
                return self.targets[i + 1] if i + 1 < len(self.targets) else None
        return None

    @staticmethod
    def match(offers: List[Any], target: dict) -> Optional[Any]:
        want_class = str(target.get("class_id") or "").strip()
        for o in offers:
            if str(o.course_id) != str(target["course_id"]):
                continue
            if want_class and str(o.class_id) != want_class:
                continue
            return o
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
