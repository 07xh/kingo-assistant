"""Local latency instrumentation for read-only polls.

Why this exists: the benchmark matrix in ``tools/bench`` shows that discovery
latency, not submission latency, decides the outcome of a seat race
(28 100 ms baseline vs 281 ms with correct polling, while submit stays ~20 ms).
That conclusion is worthless without per-run measurement, so every poll now
emits one JSONL row split into budget-wait / request / decode / parse / total.

Privacy: records never contain course identifiers, course names, cookies,
request bodies or response bodies. Only timings, byte counts and row counts.

Records are appended, size-capped and rotated; the file stays local.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

MAX_RECORD_BYTES = 512 * 1024
DEFAULT_LATENCY_FILE = "latency.jsonl"

#: Phases measured per poll. ``budget_wait`` is local quota/lease waiting and
#: must be read as "time we threw away before touching the network".
PHASES: tuple[str, ...] = (
    "budget_wait_ms", "request_ms", "decode_ms", "parse_ms", "total_ms",
)


def percentiles(values: Sequence[float]) -> dict[str, float]:
    """Nearest-rank percentiles; empty input yields zeros, never a crash."""
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return {"count": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0, "max": 0.0,
                "mean": 0.0}
    def pick(fraction: float) -> float:
        index = min(len(clean) - 1, max(0, int(round(fraction * (len(clean) - 1)))))
        return round(clean[index], 3)
    return {
        "count": len(clean),
        "p50": pick(0.50),
        "p90": pick(0.90),
        "p95": pick(0.95),
        "max": round(clean[-1], 3),
        "mean": round(sum(clean) / len(clean), 3),
    }


@dataclass(frozen=True, slots=True)
class LatencySample:
    """One completed read-only poll."""

    kind: str                     # identity | catalog | entry | probe
    status: int | None = None
    budget_wait_ms: float = 0.0
    request_ms: float = 0.0
    decode_ms: float = 0.0
    parse_ms: float = 0.0
    total_ms: float = 0.0
    bytes_read: int = 0
    rows: int = 0
    note: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip() or len(self.kind) > 40:
            raise ValueError("时延采样类型无效")
        for name in PHASES:
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value < 0 or value > 3_600_000:
                raise ValueError(f"时延采样 {name} 无效")
        if self.status is not None and (type(self.status) is not int
                                        or not 100 <= self.status <= 599):
            raise ValueError("时延采样状态码无效")
        for value, label, limit in ((self.bytes_read, "字节数", 100_000_000),
                                    (self.rows, "行数", 1_000_000)):
            if type(value) is not int or not 0 <= value <= limit:
                raise ValueError(f"时延采样{label}无效")
        if not isinstance(self.note, str) or len(self.note) > 120:
            raise ValueError("时延采样备注无效")

    def to_mapping(self) -> dict:
        return {
            "at": self.at.isoformat(),
            "kind": self.kind,
            "status": self.status,
            "budget_wait_ms": round(self.budget_wait_ms, 3),
            "request_ms": round(self.request_ms, 3),
            "decode_ms": round(self.decode_ms, 3),
            "parse_ms": round(self.parse_ms, 3),
            "total_ms": round(self.total_ms, 3),
            "bytes_read": self.bytes_read,
            "rows": self.rows,
            "note": self.note,
        }


class LatencyRecorder:
    """Append-only JSONL sink with a hard size cap.

    A recorder failure must never break a poll, so every write is best-effort
    and exceptions are swallowed after the first one is remembered.
    """

    def __init__(self, path: Path | str | None,
                 max_bytes: int = MAX_RECORD_BYTES) -> None:
        self.path = Path(path) if path else None
        self.max_bytes = max_bytes
        self.samples: list[LatencySample] = []
        self.last_error: str = ""
        self._writable = self.path is not None
        if self._writable:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                self._writable = False
                self.last_error = type(error).__name__

    @property
    def enabled(self) -> bool:
        return self._writable

    def record(self, sample: LatencySample) -> None:
        self.samples.append(sample)
        if not self._writable or self.path is None:
            return
        try:
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                rotated = self.path.with_suffix(self.path.suffix + ".1")
                os.replace(self.path, rotated)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(sample.to_mapping(), ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
        except OSError as error:
            self._writable = False
            self.last_error = type(error).__name__

    def summary(self, *, kind: str | None = None) -> dict:
        subset = [s for s in self.samples if kind is None or s.kind == kind]
        if not subset:
            return {"samples": 0, "kinds": {}, "phases": {}}
        phases = {name: percentiles([getattr(s, name) for s in subset])
                  for name in PHASES}
        kinds: dict[str, int] = {}
        for sample in subset:
            kinds[sample.kind] = kinds.get(sample.kind, 0) + 1
        # total_ms is the sum of the others, so it can never lose a max()
        # comparison; the useful answer is the dominant *component*.
        components = [name for name in PHASES if name != "total_ms"]
        return {
            "samples": len(subset),
            "kinds": kinds,
            "phases": phases,
            "bottleneck": max(components, key=lambda name: phases[name]["p50"]),
        }

    def report(self) -> dict:
        """Overall + per-phase summary suitable for the GUI diagnostics tab."""
        return {
            "file": str(self.path) if self.path else "",
            "enabled": self._writable,
            "last_error": self.last_error,
            "overall": self.summary(),
            "by_kind": {kind: self.summary(kind=kind)
                        for kind in sorted({s.kind for s in self.samples})},
        }


def summarize_file(path: Path | str, *, kind: str | None = None) -> dict:
    """Summarise a previously written JSONL file without loading it all."""
    records: list[dict] = []
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and (kind is None or row.get("kind") == kind):
                    records.append(row)
    except OSError:
        return {"samples": 0, "kinds": {}, "phases": {}}
    phases = {
        name: percentiles([row.get(name, 0) for row in records
                           if isinstance(row.get(name, 0), (int, float))])
        for name in PHASES
    }
    kinds: dict[str, int] = {}
    for row in records:
        key = str(row.get("kind", "?"))
        kinds[key] = kinds.get(key, 0) + 1
    return {"samples": len(records), "kinds": kinds, "phases": phases}


@dataclass(frozen=True, slots=True)
class HandshakeProfile:
    """One-shot DNS/TCP/TLS split, measured separately from the HTTP client.

    httpx does not expose these sub-timings after the fact, so the prewarm step
    measures them with a throwaway socket. Values are advisory diagnostics, not
    per-request measurements.
    """

    host: str
    dns_ms: float
    tcp_ms: float
    tls_ms: float
    total_ms: float
    error: str = ""

    def as_dict(self) -> dict:
        return {"host": self.host, "dns_ms": round(self.dns_ms, 3),
                "tcp_ms": round(self.tcp_ms, 3), "tls_ms": round(self.tls_ms, 3),
                "total_ms": round(self.total_ms, 3), "error": self.error}


def handshake_profile(host: str, port: int = 443, *, timeout: float = 5.0,
                      verify: bool = True) -> HandshakeProfile:
    """Measure DNS, TCP connect and TLS handshake once. Never sends a request."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("握手探测主机无效")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("握手探测端口无效")
    started = time.perf_counter()
    try:
        dns_start = time.perf_counter()
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns_ms = (time.perf_counter() - dns_start) * 1000.0
        if not infos:
            raise OSError("no address")
        family, socktype, proto, _, address = infos[0]
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            tcp_start = time.perf_counter()
            sock.connect(address)
            tcp_ms = (time.perf_counter() - tcp_start) * 1000.0
            context = ssl.create_default_context()
            if not verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            tls_start = time.perf_counter()
            with context.wrap_socket(sock, server_hostname=host):
                tls_ms = (time.perf_counter() - tls_start) * 1000.0
        finally:
            try:
                sock.close()
            except OSError:
                pass
        return HandshakeProfile(host=host, dns_ms=dns_ms, tcp_ms=tcp_ms,
                                tls_ms=tls_ms,
                                total_ms=(time.perf_counter() - started) * 1000.0)
    except (OSError, ssl.SSLError) as error:
        return HandshakeProfile(host=host, dns_ms=0.0, tcp_ms=0.0, tls_ms=0.0,
                                total_ms=(time.perf_counter() - started) * 1000.0,
                                error=type(error).__name__)


def merge_summaries(items: Iterable[dict]) -> dict:
    """Combine phase percentiles from several runs (max of p95 is conservative)."""
    rows = [item for item in items if item and item.get("samples")]
    if not rows:
        return {"samples": 0, "kinds": {}, "phases": {}}
    phases = {}
    for name in PHASES:
        values = [row["phases"][name] for row in rows if name in row.get("phases", {})]
        phases[name] = {
            "count": sum(int(v.get("count", 0)) for v in values),
            "p50": round(sum(v.get("p50", 0.0) for v in values) / len(values), 3)
            if values else 0.0,
            "p90": round(max((v.get("p90", 0.0) for v in values), default=0.0), 3),
            "p95": round(max((v.get("p95", 0.0) for v in values), default=0.0), 3),
            "max": round(max((v.get("max", 0.0) for v in values), default=0.0), 3),
            "mean": round(sum(v.get("mean", 0.0) for v in values) / len(values), 3)
            if values else 0.0,
        }
    kinds: dict[str, int] = {}
    for row in rows:
        for key, count in row.get("kinds", {}).items():
            kinds[key] = kinds.get(key, 0) + int(count)
    return {"samples": sum(int(r.get("samples", 0)) for r in rows),
            "kinds": kinds, "phases": phases}
