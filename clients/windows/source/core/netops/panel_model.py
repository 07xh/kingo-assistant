"""Pure presentation logic for the network/time tools panel.

Widgets stay in ``windows_netops_panel``; everything that can be decided
without Tk lives here so it is testable without a display and without a
network.
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse

MAX_HOSTS = 512
_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}"
                      r"[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}"
                      r"[A-Za-z0-9])?)*$")


def _valid_host(value):
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        pass
    return bool(_HOST_RE.match(value))


def parse_hosts(text, limit=MAX_HOSTS):
    """Return ordered unique hosts from pasted text, inline comments allowed.

    Lines may be ``host``, ``host:port``, ``host  note`` or ``# comment``.
    Ports and notes are dropped: the sweep always probes the TLS port so the
    numbers stay comparable.
    """
    hosts = []
    seen = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        token = line.split()[0].strip().strip(",;")
        if token.count(":") == 1:
            token = token.split(":", 1)[0]
        if not _valid_host(token):
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        hosts.append(token)
        if len(hosts) >= limit:
            break
    return hosts


def format_race_row(row):
    """(host, median_ms, ok, fail) -> display tuple for the Treeview."""
    host, median_ms, ok, fail = row
    return (host, "%.0f ms" % median_ms, str(ok), str(fail))


def summarize_race(ranked, total_candidates=0):
    if not ranked:
        if total_candidates:
            return "测速完成：%d 个候选全部不可用" % total_candidates
        return "测速完成：没有可用节点"
    host, median_ms, _ok, _fail = ranked[0]
    text = "最优 %s · %.0f ms · 可用 %d 个" % (host, median_ms, len(ranked))
    if total_candidates:
        text += "／候选 %d 个" % total_candidates
    return text


def offset_tone(offset_ms):
    """Tone bucket for the clock badge: ok / warn / danger."""
    if offset_ms is None:
        return "muted"
    magnitude = abs(float(offset_ms))
    if magnitude <= 250:
        return "ok"
    if magnitude <= 1000:
        return "warn"
    return "danger"


def offset_text(result):
    """Human sentence for an estimate() result dict."""
    offset = result.get("offset")
    if offset is None:
        return "校准失败：目标未返回 Date／Server-Timing 头，或全部请求超时"
    offset_ms = offset * 1000.0
    direction = "本机慢" if offset_ms > 0 else "本机快"
    return "偏移 %+.0f ms（%s）· 样本 %d · 中位 RTT %.0f ms" % (
        offset_ms, direction, result.get("n") or 0,
        (result.get("rtt_median") or 0.0) * 1000.0)


def offset_badge_text(offset_ms):
    if offset_ms is None:
        return "时钟未校准"
    if abs(offset_ms) <= 250:
        return "时钟已校准 ±250ms"
    return "时钟偏差 %.0fms" % abs(offset_ms)


def guard_text(stats, running):
    prefix = "守护运行中" if running else "守护已停止"
    return "%s · 检查 %d · 下线 %d · 重登 %d/%d" % (
        prefix, stats.get("checks", 0), stats.get("auth_lost", 0),
        stats.get("relogin_ok", 0),
        stats.get("relogin_ok", 0) + stats.get("relogin_fail", 0))


def mask_url(url):
    """Strip query and fragment before showing a probe target in the UI."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if not parts.scheme or not parts.netloc:
        return (url or "").strip()[:80]
    path = parts.path or "/"
    if len(path) > 48:
        path = path[:45] + "..."
    return "%s://%s%s" % (parts.scheme, parts.netloc, path)
