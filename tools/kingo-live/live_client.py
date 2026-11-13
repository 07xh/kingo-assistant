#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_client.py - real HTTP client for an academic course-selection system.

This is NOT a simulator. Every request in this module is a real HTTP/HTTPS
request issued through http.client. Sessions, proxies, cookies, rate budgets,
submission bodies and enrollment readback are all real.

Four latency stages are instrumented on the real request path:

    client_ms   request construction (serialize body, assemble headers)
    connect_ms  DNS + TCP + TLS (or proxy CONNECT tunnel)
    network_ms  wire time from send to first response byte, minus server time
    queue_ms    server admission queue  (only when Server-Timing exposes it)
    tx_ms       database transaction   (only when Server-Timing exposes it)
    server_unresolved_ms  used when the backend sends no Server-Timing header

Three speeds are recorded per attempt:

    discover_ms  first observation of a free seat
    submit_ms    decision -> submission response headers
    confirm_ms   submission -> official enrollment-list readback match

Configuration values that depend on the target deployment are placeholders.
See config.example.json for how to locate each one and how to verify it.

Python 3.10+ / standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import secrets
import sqlite3
import ssl
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import optimizations as opt

if os.name == "nt":
    import msvcrt
else:
    import fcntl


# ---------------------------------------------------------------------------
# Cross-process file lease (real OS lock, not a simulated semaphore)
# ---------------------------------------------------------------------------

class Lease:
    """Single in-flight request across cooperating processes on one machine."""

    def __init__(self, path: str):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "a+b")
        if os.name == "nt":
            self.fh.seek(0)
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        if os.name == "nt":
            self.fh.seek(0)
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()
        return False


# ---------------------------------------------------------------------------
# Request budget: SQLite sliding log + persisted Retry-After cooldown
# ---------------------------------------------------------------------------

class RequestBudget:
    def __init__(self, db_path: str, rate: float, burst: int, retry_after_ms: float):
        self.db_path = os.path.abspath(db_path)
        self.rate = float(rate)
        self.burst = int(burst)
        self.retry_after_ms = float(retry_after_ms)
        self.lease = Lease(self.db_path + ".lease")
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (ts REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cooldown "
                       "(key TEXT PRIMARY KEY, until REAL NOT NULL)")
            db.commit()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10.0)

    def reserve(self) -> Tuple[bool, float, str]:
        """Return (allowed, wait_ms, reason)."""
        now = time.time()
        with self.lease:
            with self._lock, closing(self._connect()) as db:
                row = db.execute(
                    "SELECT until FROM cooldown WHERE key='global'").fetchone()
                if row and row[0] > now:
                    return False, (row[0] - now) * 1000.0, "retry_after"
                db.execute("DELETE FROM requests WHERE ts < ?", (now - 1.0,))
                count = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
                burst_count = db.execute(
                    "SELECT COUNT(*) FROM requests WHERE ts > ?",
                    (now - 0.05,)).fetchone()[0]
                if count >= self.rate or burst_count >= self.burst:
                    return False, max(1.0, 1000.0 / max(1.0, self.rate)), "budget"
                db.execute("INSERT INTO requests(ts) VALUES(?)", (now,))
                db.commit()
        return True, 0.0, "ok"

    def cooldown(self, retry_after_header_ms: float = 0.0) -> float:
        until = time.time() + max(self.retry_after_ms, retry_after_header_ms) / 1000.0
        with self.lease:
            with self._lock, closing(self._connect()) as db:
                db.execute("INSERT OR REPLACE INTO cooldown(key, until) "
                           "VALUES('global', ?)", (until,))
                db.commit()
        return max(self.retry_after_ms, retry_after_header_ms)


# ---------------------------------------------------------------------------
# Real HTTP transport with stage instrumentation
# ---------------------------------------------------------------------------

@dataclass
class RawResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    client_ms: float = 0.0
    connect_ms: float = 0.0
    ttfb_ms: float = 0.0
    transfer_ms: float = 0.0
    network_ms: float = 0.0
    queue_ms: Optional[float] = None
    tx_ms: Optional[float] = None
    server_unresolved_ms: float = 0.0
    server_timing: str = ""
    proxy: str = ""
    reused: bool = False
    error: str = ""


def parse_server_timing(value: str) -> Dict[str, float]:
    """Parse 'queue;dur=12.3, db;dur=30.1' into a metric map."""
    out: Dict[str, float] = {}
    for part in value.split(","):
        bits = [b.strip() for b in part.split(";")]
        if not bits or not bits[0]:
            continue
        dur = None
        for b in bits[1:]:
            if b.startswith("dur="):
                try:
                    dur = float(b[4:])
                except ValueError:
                    dur = None
        if dur is not None:
            out[bits[0].lower()] = dur
    return out


class Transport:
    """One real connection per call. Supports direct and CONNECT-proxy paths."""

    def __init__(self, base_url: str, cookie: str = "", timeout: float = 5.0,
                 extra_headers: Optional[Dict[str, str]] = None,
                 proxies: Optional[List[str]] = None, verify_tls: bool = True,
                 keep_alive: bool = False):
        u = urllib.parse.urlparse(base_url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError(f"invalid base_url: {base_url!r}")
        self.scheme = u.scheme
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.base_path = u.path.rstrip("/")
        self.timeout = timeout
        self.cookie = cookie
        self.extra_headers = extra_headers or {}
        self.proxies = proxies or []
        self.ctx = ssl.create_default_context()
        if not verify_tls:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self._rr = 0
        self.keep_alive = keep_alive
        self._persistent: Optional[http.client.HTTPConnection] = None
        self._persistent_proxy = ""

    def _drop_persistent(self) -> None:
        if self._persistent is not None:
            try:
                self._persistent.close()
            except Exception:
                pass
        self._persistent = None
        self._persistent_proxy = ""

    def _pick_proxy(self) -> str:
        if not self.proxies:
            return ""
        p = self.proxies[self._rr % len(self.proxies)]
        self._rr += 1
        return p

    def _open(self) -> Tuple[http.client.HTTPConnection, float, str, bool]:
        if self.keep_alive and self._persistent is not None:
            return self._persistent, 0.0, self._persistent_proxy, True
        t0 = time.perf_counter()
        proxy = self._pick_proxy()
        if proxy:
            pu = urllib.parse.urlparse(proxy if "://" in proxy else "http://" + proxy)
            ph, pp = pu.hostname, pu.port or 80
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(ph, pp, timeout=self.timeout,
                                                  context=self.ctx)
                conn.set_tunnel(self.host, self.port)
            else:
                conn = http.client.HTTPConnection(ph, pp, timeout=self.timeout)
        else:
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(self.host, self.port,
                                                  timeout=self.timeout, context=self.ctx)
            else:
                conn = http.client.HTTPConnection(self.host, self.port,
                                                  timeout=self.timeout)
        conn.connect()
        if self.keep_alive:
            self._persistent = conn
            self._persistent_proxy = proxy
        return conn, (time.perf_counter() - t0) * 1000.0, proxy, False

    def request(self, method: str, path: str,
                body: Optional[bytes] = None,
                headers: Optional[Dict[str, str]] = None,
                build_ms: float = 0.0) -> RawResponse:
        t0 = time.perf_counter()
        h: Dict[str, str] = {
            "User-Agent": "KingoLive/1.0",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Connection": "keep-alive" if self.keep_alive else "close",
        }
        if self.cookie:
            h["Cookie"] = self.cookie
        h.update(self.extra_headers)
        if headers:
            h.update(headers)
        if body is not None and "Content-Type" not in h:
            h["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if body is not None:
            h["Content-Length"] = str(len(body))
        client_ms = (time.perf_counter() - t0) * 1000.0 + build_ms

        full_path = self.base_path + (path if path.startswith("/") else "/" + path)
        # A plain HTTP proxy needs an absolute request target. A CONNECT tunnel
        # (https) keeps origin-form, which _open() already established.
        if self.proxies and self.scheme == "http":
            full_path = f"{self.scheme}://{self.host}:{self.port}{full_path}"
        conn = None
        try:
            conn, connect_ms, proxy, reused = self._open()
            t_send = time.perf_counter()
            conn.request(method, full_path, body=body, headers=h)
            resp = conn.getresponse()
            ttfb_ms = (time.perf_counter() - t_send) * 1000.0
            raw_headers = {k.lower(): v for k, v in resp.getheaders()}
            t_read = time.perf_counter()
            data = resp.read()
            transfer_ms = (time.perf_counter() - t_read) * 1000.0

            st = raw_headers.get("server-timing", "")
            metrics = parse_server_timing(st)
            queue_ms = metrics.get("queue")
            tx_ms = metrics.get("db", metrics.get("tx", metrics.get("sql")))
            server_known = sum(v for k, v in metrics.items()
                               if k in ("queue", "db", "tx", "sql"))
            network_ms = max(0.0, ttfb_ms - server_known)
            unresolved = max(0.0, ttfb_ms - network_ms) if not metrics else 0.0

            return RawResponse(
                status=resp.status, headers=raw_headers, body=data,
                client_ms=client_ms, connect_ms=connect_ms, ttfb_ms=ttfb_ms,
                transfer_ms=transfer_ms, network_ms=network_ms,
                queue_ms=queue_ms, tx_ms=tx_ms,
                server_unresolved_ms=unresolved, server_timing=st, proxy=proxy,
                reused=reused)
        except Exception as exc:
            self._drop_persistent()
            return RawResponse(status=0, headers={}, body=b"",
                               client_ms=client_ms, error=f"{type(exc).__name__}: {exc}")
        finally:
            if conn is not None and not self.keep_alive:
                conn.close()


# ---------------------------------------------------------------------------
# Catalog parsing (real HTML tables or real JSON responses)
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


@dataclass
class Offering:
    course_id: str
    class_id: str
    remain: Optional[int]
    name: str = ""
    teacher: str = ""
    source_hash: str = ""

    def fingerprint(self) -> str:
        return f"{self.course_id}|{self.class_id}"


def dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if part == "":
            continue
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(f"cannot walk {part!r} into {type(cur).__name__}")
    return cur


def parse_remain(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "--", "无", "未知", "N/A"):
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def parse_catalog(cfg: dict, resp: RawResponse) -> List[Offering]:
    text = resp.body.decode(cfg.get("encoding", "utf-8"), errors="replace")
    columns: Dict[str, int] = cfg.get("columns", {})
    out: List[Offering] = []
    fmt = cfg.get("format", "json")
    if fmt == "json":
        raw = json.loads(text)
        rows = dig(raw, cfg.get("json_list_path", ""))
        for row in rows:
            def g(key):
                idx = columns.get(key)
                if idx is None:
                    return ""
                if isinstance(row, dict):
                    return row.get(str(idx), "")
                if isinstance(row, list):
                    return row[int(idx)] if str(idx).isdigit() and int(idx) < len(row) else ""
                return ""
            out.append(Offering(
                course_id=str(g("course_id")), class_id=str(g("class_id")),
                remain=parse_remain(g("remain")), name=str(g("course_name")),
                teacher=str(g("teacher")),
                source_hash=hashlib.sha256(text.encode()).hexdigest()[:16]))
    else:
        p = _TableParser()
        p.feed(text)
        for row in p.rows:
            def cell(key):
                idx = columns.get(key)
                return row[idx] if idx is not None and idx < len(row) else ""
            cid = str(cell("course_id"))
            if not cid:
                continue
            out.append(Offering(
                course_id=cid, class_id=str(cell("class_id")),
                remain=parse_remain(cell("remain")),
                name=str(cell("course_name")), teacher=str(cell("teacher")),
                source_hash=hashlib.sha256(text.encode()).hexdigest()[:16]))
    return out


# ---------------------------------------------------------------------------
# Durable intent journal - fingerprints only, no secrets or raw records
# ---------------------------------------------------------------------------

class Journal:
    def __init__(self, path: str, salt: bytes = b""):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.salt = salt or secrets.token_bytes(32)

    def fp(self, value: str) -> str:
        return hmac.new(self.salt, value.encode(), hashlib.sha256).hexdigest()[:24]

    def _write_fp(self, state: str, fp: str, note: str = "") -> None:
        rec = {"ts": round(time.time(), 6), "state": state,
               "fp": fp, "note": note}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def write(self, state: str, offering: Offering, note: str = "") -> None:
        self._write_fp(state, self.fp(offering.fingerprint()), note)

    def resolve(self, state: str, fp: str, note: str = "") -> None:
        """Reconcile a hashed intent without ever storing the plaintext id."""
        self._write_fp(state, fp, note)

    def pending_fingerprints(self) -> set:
        """All intents that still need an official readback verdict."""
        if not self.path.exists():
            return set()
        states: Dict[str, set] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            states.setdefault(r.get("fp", ""), set()).add(r.get("state", ""))
        return {fp for fp, s in states.items()
                if "pending" in s and not (s & {"confirmed", "rejected"})}

    def is_pending(self, offering: Offering) -> bool:
        return self.fp(offering.fingerprint()) in self.pending_fingerprints()


# ---------------------------------------------------------------------------
# Engine: real strategies, real requests, real confirmation
# ---------------------------------------------------------------------------

@dataclass
class Strategy:
    poll_interval_ms: float = 60000.0
    concurrency: int = 1
    burst: int = 0
    sessions: int = 1
    proxies: List[str] = field(default_factory=list)
    respect_retry_after: bool = True
    prebuild: bool = False
    race_repeat: int = 1
    direct_api: bool = False
    timeout: float = 5.0
    cycles: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "Strategy":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Attempt:
    ts: float
    action: str
    discover_ms: float = 0.0
    submit_ms: float = 0.0
    confirm_ms: float = 0.0
    client_ms: float = 0.0
    connect_ms: float = 0.0
    network_ms: float = 0.0
    queue_ms: Optional[float] = None
    tx_ms: Optional[float] = None
    server_unresolved_ms: float = 0.0
    server_timing: str = ""
    status: int = 0
    success: bool = False
    reason: str = ""
    course_id: str = ""
    class_id: str = ""
    proxy: str = ""
    error: str = ""

    def add_stages(self, r: RawResponse) -> None:
        self.client_ms += r.client_ms
        self.connect_ms += r.connect_ms
        self.network_ms += r.network_ms
        self.server_unresolved_ms += r.server_unresolved_ms
        self.server_timing = self.server_timing or r.server_timing
        self.proxy = self.proxy or r.proxy
        if r.queue_ms is not None:
            self.queue_ms = (self.queue_ms or 0.0) + r.queue_ms
        if r.tx_ms is not None:
            self.tx_ms = (self.tx_ms or 0.0) + r.tx_ms


class Engine:
    def __init__(self, cfg: dict, records_path: str = "kingo-records.jsonl"):
        self.cfg = cfg
        self.st = Strategy.from_dict(cfg.get("strategy", {}))
        self.cookie = os.environ.get("KINGO_COOKIE", cfg.get("cookie", ""))
        self.budget = RequestBudget(
            cfg.get("budget", {}).get("database", "kingo-budget.sqlite"),
            cfg.get("budget", {}).get("rate", 20.0),
            cfg.get("budget", {}).get("burst", 5),
            cfg.get("budget", {}).get("retry_after_ms", 1000.0))
        self.journal = Journal(cfg.get("journal", {}).get("path", "kingo-intents.jsonl"))
        self.records_path = Path(records_path)
        self._rec_lock = threading.Lock()
        self.last_probe_body = b""
        self.last_probe_headers: Dict[str, str] = {}
        if self.st.direct_api:
            # Real JSON channel: skip the HTML form/table layer entirely.
            self.cfg = json.loads(json.dumps(cfg))
            self.cfg["catalog"]["format"] = "json"
            self.cfg.setdefault("headers", {})["Accept"] = "application/json"
            self.cfg["headers"]["X-Requested-With"] = "XMLHttpRequest"
        self.prewarmer = opt.Prewarmer(self.cfg)
        self.classifier = opt.FailureClassifier(self.cfg)
        self.release_monitor = opt.ReleaseMonitor()
        self.clock = opt.ClockSync(
            header=self.cfg.get("clock", {}).get("header", "Date"),
            samples_target=int(self.cfg.get("clock", {}).get("samples", 5)))
        self.planner = opt.FailoverPlanner(self.cfg.get("targets", []))
        self.transports = self._build_transports()

    def _build_transports(self) -> List[Transport]:
        out = []
        for i in range(max(1, self.st.sessions)):
            cookie = self.cookie
            if i > 0:
                env_key = f"KINGO_COOKIE_{i}"
                cookie = os.environ.get(env_key, "")
            out.append(Transport(
                self.cfg["base_url"], cookie=cookie, timeout=self.st.timeout,
                extra_headers=self.cfg.get("headers", {}),
                proxies=self.st.proxies,
                verify_tls=self.cfg.get("verify_tls", True),
                keep_alive=self.prewarmer.keep_alive))
        return out

    def record(self, a: Attempt) -> None:
        with self._rec_lock:
            with open(self.records_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(a), ensure_ascii=False) + "\n")

    def close(self) -> None:
        """Release persistent connections held by prewarming."""
        for tr in self.transports:
            tr._drop_persistent()

    def _apply_cooldown(self, r: RawResponse) -> None:
        if r.status in (429, 503):
            ra = r.headers.get("retry-after", "")
            ms = float(ra) * 1000.0 if ra.isdigit() else 0.0
            self.budget.cooldown(ms)

    def _budgeted(self, tr: Transport, method: str, path: str,
                  body: Optional[bytes] = None,
                  headers: Optional[Dict[str, str]] = None,
                  build_ms: float = 0.0) -> RawResponse:
        allowed, wait_ms, reason = self.budget.reserve()
        if not allowed:
            if reason == "retry_after" and not self.st.respect_retry_after:
                # Explicitly ignore Retry-After: issue the physical request anyway.
                r = tr.request(method, path, body=body, headers=headers,
                               build_ms=build_ms)
                self._apply_cooldown(r)
                return r
            time.sleep(min(wait_ms, 5000.0) / 1000.0)
            allowed, _, reason = self.budget.reserve()
        if not allowed:
            return RawResponse(status=429, headers={}, body=b"", error="budget:" + reason)
        r = tr.request(method, path, body=body, headers=headers, build_ms=build_ms)
        self._apply_cooldown(r)
        return r

    def budgeted(self, tr: Transport, method: str, path: str,
                 body: Optional[bytes] = None,
                 headers: Optional[Dict[str, str]] = None,
                 build_ms: float = 0.0) -> RawResponse:
        """Public budgeted request entry point for strategy modules."""
        return self._budgeted(tr, method, path, body=body, headers=headers,
                              build_ms=build_ms)

    # ---- real read-only probe ---------------------------------------------
    def probe(self) -> Attempt:
        tr = self.transports[0]
        cat = self.cfg["catalog"]
        t0 = time.perf_counter()
        r = self._budgeted(tr, cat.get("method", "GET"), cat["path"])
        self.last_probe_body = r.body
        self.last_probe_headers = dict(r.headers)
        a = Attempt(ts=time.time(), action="probe", status=r.status, error=r.error)
        a.add_stages(r)
        a.discover_ms = (time.perf_counter() - t0) * 1000.0
        if r.status == 200 and not r.error:
            try:
                offers = parse_catalog(cat, r)
                a.reason = f"parsed={len(offers)}"
            except Exception as exc:
                a.reason = f"parse_error={type(exc).__name__}: {exc}"
        self.record(a)
        return a

    # ---- real monitoring ---------------------------------------------------
    def monitor(self, cycles: int = 0) -> List[Attempt]:
        cycles = cycles or self.st.cycles
        cat = self.cfg["catalog"]
        targets = {t["course_id"]: t for t in self.cfg.get("targets", [])}
        found: List[Attempt] = []
        seat_open_at = time.perf_counter()
        interval = max(0.0, self.st.poll_interval_ms / 1000.0)
        for c in range(cycles):
            tr = self.transports[c % len(self.transports)]
            t0 = time.perf_counter()
            r = self._budgeted(tr, cat.get("method", "GET"), cat["path"])
            a = Attempt(ts=time.time(), action="monitor", status=r.status, error=r.error)
            a.add_stages(r)
            if r.status == 200 and not r.error:
                try:
                    offers = parse_catalog(cat, r)
                except Exception as exc:
                    offers = []
                    a.reason = f"parse_error={exc}"
                for o in offers:
                    if o.course_id in targets and (o.remain or 0) > 0:
                        a.discover_ms = (time.perf_counter() - seat_open_at) * 1000.0
                        a.reason = f"seat={o.remain} fp={o.fingerprint()}"
                        a.course_id, a.class_id = o.course_id, o.class_id
                        found.append(a)
                        break
            self.record(a)
            if found:
                break
            if interval > 0 and c + 1 < cycles:
                time.sleep(interval)
        return found or []

    # ---- real enrollment ---------------------------------------------------
    def _submit_body(self, target: dict, offering: Offering) -> bytes:
        sub = self.cfg["submit"]
        values = {
            "course_id": offering.course_id,
            "class_id": offering.class_id,
            "course_name": offering.name,
            "teacher": offering.teacher,
            "xnxq": self.cfg.get("xnxq", ""),
            "token": self.cfg.get("token", ""),
        }
        values.update({k: str(v) for k, v in target.items()})
        if sub.get("encoding", "form") == "json":
            payload = {k: v.format(**values) if isinstance(v, str) else v
                       for k, v in sub.get("fields", {}).items()}
            return json.dumps(payload, ensure_ascii=False).encode("utf-8")
        pairs = {k: (v.format(**values) if isinstance(v, str) else str(v))
                 for k, v in sub.get("fields", {}).items()}
        return urllib.parse.urlencode(pairs, encoding="utf-8").encode("utf-8")

    def _readback(self, tr: Transport, offering: Offering) -> Tuple[bool, RawResponse]:
        en = self.cfg["enrollment"]
        r = self._budgeted(tr, en.get("method", "GET"), en["path"])
        if r.status != 200 or r.error:
            return False, r
        text = r.body.decode(en.get("encoding", "utf-8"), errors="replace")
        rows: List[Any] = []
        try:
            if en.get("format", "json") == "json":
                rows = dig(json.loads(text), en.get("json_list_path", ""))
            else:
                p = _TableParser(); p.feed(text); rows = p.rows
        except Exception:
            return False, r
        cols = en.get("columns", {})
        for row in rows:
            def g(key):
                idx = cols.get(key)
                if idx is None:
                    return ""
                if isinstance(row, dict):
                    return str(row.get(str(idx), ""))
                if isinstance(row, (list, tuple)):
                    return str(row[idx]) if isinstance(idx, int) and idx < len(row) else ""
                return ""
            if (g("course_id") == offering.course_id
                    and (not offering.class_id or g("class_id") == offering.class_id)):
                return True, r
        return False, r

    @staticmethod
    def _target_key(target: dict) -> str:
        return f"{target.get('course_id', '')}|{target.get('class_id', '')}"

    def _prebuild_bodies(self) -> Dict[str, Tuple[Offering, bytes]]:
        """Serialize real submission bodies OUTSIDE the timed window."""
        cat = self.cfg["catalog"]
        out: Dict[str, Tuple[Offering, bytes]] = {}
        if not self.st.prebuild:
            return out
        for target in self.planner.targets:
            r0 = self._budgeted(self.transports[0],
                                cat.get("method", "GET"), cat["path"])
            if r0.status != 200 or r0.error:
                continue
            try:
                offers = parse_catalog(cat, r0)
            except Exception:
                continue
            o = opt.FailoverPlanner.match(offers, target)
            if o is not None:
                out[self._target_key(target)] = (o, self._submit_body(target, o))
        return out

    def _attempt_target(self, target: dict,
                        prebuilt: Dict[str, Tuple[Offering, bytes]]
                        ) -> Tuple[List[Attempt], opt.Decision]:
        """One exact target, real requests, one explicit chain decision."""
        cat, sub = self.cfg["catalog"], self.cfg["submit"]
        tr = self.transports[0]
        base = {"course_id": str(target.get("course_id", "")),
                "class_id": str(target.get("class_id", ""))}
        r = self._budgeted(tr, cat.get("method", "GET"), cat["path"])
        a = Attempt(ts=time.time(), action="enroll", status=r.status,
                    error=r.error or "", **base)
        a.add_stages(r)
        if r.status != 200 or r.error:
            a.reason = "catalog_unavailable"
            return [a], opt.Decision("unknown" if r.error else "overload",
                                     "halt", a.reason)
        try:
            offers = parse_catalog(cat, r)
        except Exception as exc:
            a.reason = f"parse_error={exc}"
            return [a], opt.Decision("unknown", "halt", a.reason)

        offering = opt.FailoverPlanner.match(offers, target)
        if offering is None:
            a.reason = "no_matching_offering"
            return [a], opt.Decision("no_match", "next", a.reason)
        if offering.remain is None or offering.remain <= 0:
            a.reason = "seat_unknown_or_zero"
            return [a], opt.Decision("seat_unavailable", "next", a.reason)
        if self.journal.is_pending(offering):
            a.reason = "pending_intent_needs_reconciliation"
            return [a], opt.Decision("pending", "halt", a.reason)

        key = self._target_key(target)
        if self.st.prebuild and key in prebuilt:
            offering, body = prebuilt[key]
            build_ms = 0.0
        else:
            tb = time.perf_counter()
            body = self._submit_body(target, offering)
            build_ms = (time.perf_counter() - tb) * 1000.0
        n = max(1, self.st.concurrency * self.st.race_repeat * (self.st.burst or 1))
        jobs = [(i, i % len(self.transports)) for i in range(n)]

        def one(job) -> Attempt:
            _idx, ti = job
            t0 = time.perf_counter()
            local_tr = self.transports[ti]
            headers = {"Content-Type": "application/json; charset=UTF-8"} \
                if sub.get("encoding", "form") == "json" else None
            sr = self._budgeted(local_tr, sub.get("method", "POST"),
                                sub["path"], body=body, headers=headers,
                                build_ms=build_ms)
            sa = Attempt(ts=time.time(), action="submit", status=sr.status,
                         error=sr.error, course_id=offering.course_id,
                         class_id=offering.class_id)
            sa.add_stages(sr)
            sa.submit_ms = (time.perf_counter() - t0) * 1000.0

            # Business-level rejection is known BEFORE any intent is journaled.
            pre = self.classifier.classify(sr.status, sr.body, False, sr.error)
            if pre.kind in ("business_full", "business_rejected", "window_closed"):
                sa.reason = pre.kind
                return sa
            if sr.status not in (200, 201, 302):
                sa.reason = f"not_accepted_{sr.status}"
                return sa

            self.journal.write("pending", offering, f"http={sr.status}")
            t1 = time.perf_counter()
            ok, rr = self._readback(local_tr, offering)
            sa.add_stages(rr)
            sa.confirm_ms = (time.perf_counter() - t1) * 1000.0
            sa.success = ok
            if ok:
                sa.reason = "confirmed"
                self.journal.write("confirmed", offering, f"http={rr.status}")
            elif rr.status == 200 and not rr.error:
                # Official list was read successfully and the target is absent:
                # a definite business failure, not an unknown result.
                sa.reason = "readback_no_match"
                self.journal.write("rejected", offering, f"http={rr.status}")
            else:
                # The readback itself failed. The submission result is UNKNOWN.
                # Keep the intent pending for --reconcile; never auto-retry.
                sa.reason = "readback_unavailable"
            return sa

        if n == 1:
            atts = [one(jobs[0])]
        else:
            with ThreadPoolExecutor(max_workers=max(1, self.st.concurrency)) as pool:
                futs = [pool.submit(one, j) for j in jobs]
                atts = [f.result() for f in as_completed(futs)]

        winner = next((x for x in atts if x.success), atts[0])
        d = self.classifier.classify(winner.status, b"", winner.success)
        if winner.reason in ("business_full", "business_rejected", "window_closed"):
            d = opt.Decision(winner.reason,
                             "wait" if winner.reason == "window_closed" else "next",
                             winner.reason)
        elif winner.reason in ("readback_no_match", "readback_unavailable"):
            d = opt.Decision("pending", "halt", winner.reason)
        return atts, d

    def enroll(self, confirm: bool = False) -> List[Attempt]:
        """Ordered failover chain. No target is ever retried in one run."""
        if not confirm:
            raise RuntimeError("real submission requires --confirm")
        prebuilt = self._prebuild_bodies()
        results: List[Attempt] = []
        self.planner.history = []
        backoff_ms = float(self.cfg.get("business", {}).get("backoff_ms", 200.0))
        for target in self.planner.targets:
            atts, d = self._attempt_target(target, prebuilt)
            for a in atts:
                self.record(a)
                results.append(a)
            self.planner.note(target, d, atts[0])
            if d.action in ("stop", "halt", "wait", "cooldown"):
                # Unknown results, auth failures, closed windows and rate
                # limits all stop the chain. Retrying here would amplify load
                # and could duplicate a submission whose result is unknown.
                break
            if d.action == "backoff":
                time.sleep(min(backoff_ms, 2000.0) / 1000.0)
        return results

    # ---- P1: real server-clock alignment -----------------------------------
    def sync_clock(self) -> dict:
        c = self.cfg.get("clock", {})
        cat = self.cfg["catalog"]
        path = c.get("path", cat["path"])
        method = c.get("method", cat.get("method", "GET"))
        for i in range(max(1, self.clock.samples_target)):
            tr = self.transports[i % len(self.transports)]
            t0 = time.time()
            r = self._budgeted(tr, method, path)
            t1 = time.time()
            self.clock.observe(r, t0, t1)
            if r.error:
                break
        return self.clock.report()

    # ---- P1: real session + connection prewarming --------------------------
    def warmup(self) -> dict:
        return self.prewarmer.warm(self)

    # ---- P2: real seat-release event monitoring ----------------------------
    def watch_events(self, cycles: int = 0) -> dict:
        cycles = cycles or self.st.cycles
        cat = self.cfg["catalog"]
        interval = max(0.0, self.st.poll_interval_ms / 1000.0)
        events: List[dict] = []
        for c in range(cycles):
            tr = self.transports[c % len(self.transports)]
            t0 = time.perf_counter()
            r = self._budgeted(tr, cat.get("method", "GET"), cat["path"])
            cost = (time.perf_counter() - t0) * 1000.0
            a = Attempt(ts=time.time(), action="release_scan", status=r.status,
                        error=r.error, discover_ms=cost)
            a.add_stages(r)
            observed: List[Any] = []
            if r.status == 200 and not r.error:
                try:
                    offers = parse_catalog(cat, r)
                    for e in self.release_monitor.observe(offers, cost):
                        events.append(e.as_dict())
                        ea = Attempt(ts=time.time(), action="release_event",
                                     status=200, discover_ms=cost,
                                     course_id=e.course_id, class_id=e.class_id,
                                     reason=f"release={e.kind} seat={e.remain} "
                                            f"prev={e.previous_remain}")
                        self.record(ea)
                        observed.append(e)
                except Exception as exc:
                    a.reason = f"parse_error={exc}"
            self.record(a)
            if observed:
                break
            if interval > 0 and c + 1 < cycles:
                time.sleep(interval)
        return {"monitor": self.release_monitor.report(), "events": events}

    # ---- P6: reconcile pending intents against the official list ----------
    def reconcile(self) -> dict:
        pending = self.journal.pending_fingerprints()
        en = self.cfg["enrollment"]
        r = self._budgeted(self.transports[0], en.get("method", "GET"), en["path"])
        if r.status != 200 or r.error:
            return {"pending_before": len(pending), "resolved": 0,
                    "error": r.error or f"http={r.status}"}
        text = r.body.decode(en.get("encoding", "utf-8"), errors="replace")
        try:
            if en.get("format", "json") == "json":
                rows = dig(json.loads(text), en.get("json_list_path", ""))
            else:
                p = _TableParser(); p.feed(text); rows = p.rows
        except Exception as exc:
            return {"pending_before": len(pending), "resolved": 0,
                    "error": f"parse_error={exc}"}
        cols = en.get("columns", {})
        seen = set()
        for row in rows:
            def g(key):
                idx = cols.get(key)
                if idx is None:
                    return ""
                if isinstance(row, dict):
                    return str(row.get(str(idx), ""))
                if isinstance(row, (list, tuple)):
                    return str(row[idx]) if isinstance(idx, int) and idx < len(row) else ""
                return ""
            seen.add(self.journal.fp(f"{g('course_id')}|{g('class_id')}"))
        confirmed = rejected = 0
        for fp in pending:
            if fp in seen:
                self.journal.resolve("confirmed", fp,
                                     "reconciled_from_official_readback")
                confirmed += 1
            else:
                self.journal.resolve("rejected", fp,
                                     "reconciled_from_official_readback")
                rejected += 1
        return {"pending_before": len(pending), "resolved": len(pending),
                "confirmed": confirmed, "rejected": rejected,
                "remaining_pending": len(self.journal.pending_fingerprints())}

    # ---- full real chain ---------------------------------------------------
    def auto(self, confirm: bool = False, cycles: int = 0) -> dict:
        out: Dict[str, Any] = {"clock": self.sync_clock(),
                               "warmup": self.warmup(),
                               "align": {}, "watch": {}, "enroll": [],
                               "planner": {}}
        c = self.cfg.get("clock", {})
        window = float(c.get("window_epoch", 0.0) or 0.0)
        if window > 0.0:
            out["align"] = self.clock.wait_until(
                window, float(c.get("lead_ms", 50.0)),
                float(c.get("max_wait_s", 600.0)))
        out["watch"] = self.watch_events(cycles)
        if out["watch"]["events"] and confirm:
            out["enroll"] = [asdict(a) for a in self.enroll(confirm=True)]
        out["planner"] = self.planner.report()
        return out


# ---------------------------------------------------------------------------
# Measurement over real recorded attempts
# ---------------------------------------------------------------------------

def pct(values: List[float], q: float) -> float:
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    s = sorted(vals)
    return round(s[min(len(s) - 1, int(round((len(s) - 1) * q)))], 3)


def measure(path: str) -> dict:
    rows = []
    p = Path(path)
    if not p.exists():
        return {"error": f"no records at {path}"}
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    ok = [r for r in rows if r.get("success")]
    return {
        "attempts": len(rows),
        "confirmed": len(ok),
        "discover_p50": pct([r["discover_ms"] for r in rows if r.get("discover_ms")], .5),
        "submit_p50": pct([r["submit_ms"] for r in rows if r.get("submit_ms")], .5),
        "submit_p95": pct([r["submit_ms"] for r in rows if r.get("submit_ms")], .95),
        "confirm_p50": pct([r["confirm_ms"] for r in ok], .5),
        "confirm_p95": pct([r["confirm_ms"] for r in ok], .95),
        "client_p50": pct([r["client_ms"] for r in rows], .5),
        "connect_p50": pct([r["connect_ms"] for r in rows], .5),
        "network_p50": pct([r["network_ms"] for r in rows], .5),
        "queue_p50": pct([r["queue_ms"] for r in rows if r.get("queue_ms") is not None], .5),
        "tx_p50": pct([r["tx_ms"] for r in rows if r.get("tx_ms") is not None], .5),
        "server_unresolved_p50": pct([r["server_unresolved_ms"] for r in rows], .5),
        "with_server_timing": sum(1 for r in rows if r.get("server_timing")),
        "http": {str(s): sum(1 for r in rows if r.get("status") == s)
                 for s in sorted({r.get("status", 0) for r in rows})},
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Real HTTP course-selection client")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--records", default="kingo-records.jsonl")
    ap.add_argument("--probe", action="store_true", help="one real catalog GET + schema dump")
    ap.add_argument("--probe-bytes", type=int, default=512, metavar="N",
                    help="body preview length for --probe (default 512)")
    ap.add_argument("--probe-save", metavar="PATH",
                    help="save the FULL real probe response body to a local file")
    ap.add_argument("--monitor", type=int, metavar="CYCLES", default=0)
    ap.add_argument("--enroll", action="store_true", help="real submission")
    ap.add_argument("--confirm", action="store_true", help="explicit opt-in for --enroll")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--sync-clock", action="store_true",
                    help="sample real response time headers and estimate clock offset")
    ap.add_argument("--warmup", action="store_true",
                    help="real login-state probe and connection prewarming")
    ap.add_argument("--watch-events", type=int, metavar="CYCLES", default=0,
                    help="monitor real catalog for seat-release events")
    ap.add_argument("--auto", action="store_true",
                    help="full chain: sync -> warmup -> align -> watch -> failover enroll")
    ap.add_argument("--reconcile", action="store_true",
                    help="resolve pending intents against the official enrollment list")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override strategy keys, e.g. --set poll_interval_ms=500")
    args = ap.parse_args(argv)

    if args.measure:
        print(json.dumps(measure(args.records), ensure_ascii=False, indent=2))
        return 0

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    for item in args.set:
        k, v = item.split("=", 1)
        cur = cfg["strategy"].get(k)
        if isinstance(cur, bool):
            v = v.lower() in ("1", "true", "yes")
        elif isinstance(cur, int):
            v = int(v)
        elif isinstance(cur, float):
            v = float(v)
        elif isinstance(cur, list):
            v = [x for x in v.split(",") if x]
        cfg["strategy"][k] = v

    eng = Engine(cfg, records_path=args.records)

    if args.sync_clock:
        print(json.dumps(eng.sync_clock(), ensure_ascii=False, indent=2))
        return 0
    if args.warmup:
        print(json.dumps(eng.warmup(), ensure_ascii=False, indent=2))
        return 0
    if args.watch_events:
        print(json.dumps(eng.watch_events(args.watch_events),
                         ensure_ascii=False, indent=2))
        return 0
    if args.reconcile:
        print(json.dumps(eng.reconcile(), ensure_ascii=False, indent=2))
        return 0
    if args.auto:
        if not args.confirm:
            print("--auto runs read-only stages; add --confirm to allow real "
                  "submission after a release event", file=sys.stderr)
        print(json.dumps(eng.auto(confirm=args.confirm, cycles=args.watch_events),
                         ensure_ascii=False, indent=2))
        return 0

    if args.probe:
        a = eng.probe()
        print(json.dumps(asdict(a), ensure_ascii=False, indent=2))
        cat = cfg["catalog"]
        body = eng.last_probe_body
        enc = cat.get("encoding", "utf-8")
        print(f"\nRaw body preview (first {args.probe_bytes} bytes of the same "
              f"budgeted request, total={len(body)}):")
        print("-" * 60)
        print(body[:max(0, args.probe_bytes)].decode(enc, errors="replace"))
        print("-" * 60)
        hdr = eng.last_probe_headers or {}
        clock_candidates = {k: v for k, v in hdr.items()
                            if k.lower() in ("date", "x-server-time", "x-request-time",
                                             "x-timestamp", "x-date", "server-timing")}
        print("Response headers relevant to clock alignment / stage split:")
        print(json.dumps(clock_candidates, ensure_ascii=False, indent=2))
        if args.probe_save:
            dest = Path(args.probe_save)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            print(f"Full body saved to {dest.resolve()} ({len(body)} bytes)")
        print("Fill config columns from this body. Verify: --probe returns 200 "
              "and parsed=N with N matching the official catalog rows.")
        return 0

    if args.monitor:
        found = eng.monitor(args.monitor)
        print(json.dumps([asdict(a) for a in found], ensure_ascii=False, indent=2))
        return 0

    if args.enroll:
        if not args.confirm:
            print("real submission requires --confirm", file=sys.stderr)
            return 2
        out = eng.enroll(confirm=True)
        print(json.dumps([asdict(a) for a in out], ensure_ascii=False, indent=2))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    # Windows consoles default to cp1252/gbk; --probe prints raw catalog bodies
    # that routinely contain Chinese course names. Force UTF-8 so a preview can
    # never crash the CLI with UnicodeEncodeError.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    sys.exit(main())
