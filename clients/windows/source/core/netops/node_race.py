"""Candidate-node ranking by TCP+TLS handshake latency.

Behaviour copied from a shipping ticketing client: a large candidate list is
measured concurrently, the median of several rounds decides the order, and
nodes that fail more than half the time are dropped instead of retried.

No HTTP request is sent, so this never touches a school API and never writes
an application-level log on the remote side.
"""
from __future__ import annotations

import concurrent.futures as cf
import socket
import ssl
import statistics
import time

DEFAULT_PORT = 443
DEFAULT_TIMEOUT = 2.5
FAIL_DROP_RATIO = 0.5


def tls_probe(host, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT, sni=None):
    """Return handshake time in milliseconds, or None when unreachable."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            raw.settimeout(timeout)
            if port in (443, 8443):
                context = ssl.create_default_context()
                with context.wrap_socket(raw, server_hostname=sni or host):
                    pass
        return (time.perf_counter() - start) * 1000.0
    except (OSError, ssl.SSLError, ValueError):
        return None


def rank_hosts(hosts, rounds=3, workers=32, port=DEFAULT_PORT,
               timeout=DEFAULT_TIMEOUT, sni_map=None, should_stop=None):
    """Measure every candidate and return [(host, median_ms, ok, fail)] sorted.

    ``should_stop`` is polled between rounds so a GUI can cancel a long sweep
    without leaving worker threads running.
    """
    sni_map = sni_map or {}
    samples = {host: [] for host in hosts}
    failures = {host: 0 for host in hosts}

    def measure(host):
        return host, tls_probe(host, port, timeout, sni_map.get(host))

    for _ in range(max(1, rounds)):
        if should_stop is not None and should_stop():
            break
        with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for host, value in pool.map(measure, hosts):
                if value is None:
                    failures[host] += 1
                else:
                    samples[host].append(value)

    ranked = []
    for host in hosts:
        ok = len(samples[host])
        total = ok + failures[host]
        if not total or not ok:
            continue
        if failures[host] / total > FAIL_DROP_RATIO:
            continue
        ranked.append((host, statistics.median(samples[host]), ok, failures[host]))
    ranked.sort(key=lambda row: row[1])
    return ranked


def pick(ranked, exclude=(), max_ms=None):
    """Return (host, median_ms) for the best usable node, else None."""
    excluded = set(exclude)
    for host, median_ms, _ok, _fail in ranked:
        if host in excluded:
            continue
        if max_ms is not None and median_ms > max_ms:
            continue
        return host, median_ms
    return None


def summarize_ranked(ranked):
    """One short Chinese sentence for the GUI status line."""
    if not ranked:
        return "测速完成：没有可用节点（全部超时或被剔除）"
    host, median_ms, ok, fail = ranked[0]
    return "测速完成：%d 个可用，最优 %s %.0fms（成功 %d／失败 %d）" % (
        len(ranked), host, median_ms, ok, fail)
