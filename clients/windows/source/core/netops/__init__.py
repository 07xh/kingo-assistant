"""Local network/time helpers used by the desktop tools panel.

Everything in this package is standard-library only and never starts on
import: no socket is opened until a caller invokes a probe function.
"""
from core.netops.node_race import (
    FAIL_DROP_RATIO, rank_hosts, pick, tls_probe, summarize_ranked,
)
from core.netops.clock_offset import (
    estimate as estimate_clock_offset, corrected_now, wait_until_server_ts,
    server_time_text,
)
from core.netops.session_guard import (
    SessionGuard, classify_http, OK, AUTH_LOST, BUSY, NETWORK,
)

__all__ = [
    "FAIL_DROP_RATIO", "rank_hosts", "pick", "tls_probe", "summarize_ranked",
    "estimate_clock_offset", "corrected_now", "wait_until_server_ts",
    "server_time_text",
    "SessionGuard", "classify_http", "OK", "AUTH_LOST", "BUSY", "NETWORK",
]
