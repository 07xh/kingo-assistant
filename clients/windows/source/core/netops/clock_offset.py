"""Server clock offset estimation without touching the system clock.

An enrolment window that opens on the server's clock cannot be judged with
the local clock. This module keeps an offset in memory only: nothing here
calls SetLocalTime, requires elevation, or writes a registry value.

Precision note: HTTP ``Date`` has one-second granularity, so an offset taken
from it carries up to +-500ms of truncation error on top of half the RTT.
Callers that need finer resolution should point ``url`` at an endpoint that
returns ``Server-Timing: date=<epoch>`` or a millisecond field.
"""
from __future__ import annotations

import email.utils
import http.client
import statistics
import time
import urllib.parse

DEFAULT_TIMEOUT = 3.0


def _server_time(headers):
    date = headers.get("Date")
    if date:
        try:
            return email.utils.parsedate_to_datetime(date).timestamp()
        except (TypeError, ValueError):
            pass
    timing = headers.get("Server-Timing") or ""
    for part in timing.split(","):
        part = part.strip()
        if part.startswith("date="):
            try:
                return float(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def sample_once(url, timeout=DEFAULT_TIMEOUT):
    """Return (offset_seconds, rtt_seconds) or (None, None)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None, None
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    factory = (http.client.HTTPSConnection if parts.scheme == "https"
               else http.client.HTTPConnection)
    connection = factory(parts.hostname, parts.port, timeout=timeout)
    try:
        start = time.time()
        connection.request("HEAD", path, headers={
            "User-Agent": "KingoAssistant-clock/1.0",
            "Cache-Control": "no-cache",
        })
        response = connection.getresponse()
        response.read()
        end = time.time()
        server = _server_time(response.headers)
        if server is None:
            return None, None
        return server - (start + end) / 2.0, end - start
    except (OSError, http.client.HTTPException, ValueError):
        return None, None
    finally:
        connection.close()


def estimate(url, samples=7, timeout=DEFAULT_TIMEOUT, rtt_cut=2.0, should_stop=None):
    """Return {"offset": s|None, "n": int, "rtt_median": s|None}."""
    good = []
    for _ in range(max(1, samples)):
        if should_stop is not None and should_stop():
            break
        offset, rtt = sample_once(url, timeout)
        if offset is None:
            continue
        good.append((offset, rtt))

    if not good:
        return {"offset": None, "n": 0, "rtt_median": None}

    rtt_median = statistics.median(rtt for _offset, rtt in good)
    kept = [offset for offset, rtt in good
            if rtt <= max(rtt_median * rtt_cut, 0.15)]
    if not kept:
        kept = [offset for offset, _rtt in good]
    return {"offset": statistics.median(kept), "n": len(kept),
            "rtt_median": rtt_median}


def corrected_now(offset):
    """Server-side epoch seconds derived from the local clock plus offset."""
    return time.time() + (offset or 0.0)


def wait_until_server_ts(target_epoch, offset, spin_ms=5):
    """Block until the server clock reaches ``target_epoch``; return overshoot ms."""
    while True:
        remaining = target_epoch - corrected_now(offset)
        if remaining <= 0:
            return -remaining * 1000.0
        time.sleep(min(remaining, spin_ms / 1000.0))


def server_time_text(offset):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(corrected_now(offset)))
