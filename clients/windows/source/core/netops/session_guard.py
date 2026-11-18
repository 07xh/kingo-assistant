"""Session keepalive that separates a lost login from a flaky network.

The distinction matters: re-logging in because of one timeout is how a tool
gets a rate limit or a locked account. Only an explicit authentication
failure may trigger a relogin here; 429/5xx backs off, transport errors back
off, and everything is capped.

The probe and relogin callables are injected so this module stays free of
school-specific protocol knowledge.
"""
from __future__ import annotations

import threading
import time

OK = "ok"
AUTH_LOST = "auth_lost"
BUSY = "busy"
NETWORK = "network"

_AUTH_MARKERS = (
    "未登录", "请登录", "登录已失效", "登录超时", "not login", "not_login",
    "session expired", "unauthorized", '"code":-1', '"code":401',
)
_LOGIN_PATH_MARKERS = (
    "login", "passport", "signin", "sign_in", "sso", "authserver",
    "/cas", "cas/", "auth?",
)


def classify_http(status, body_excerpt="", location=""):
    """Classify one probe response into OK／AUTH_LOST／BUSY／NETWORK."""
    if status in (401, 403):
        return AUTH_LOST
    if status in (301, 302, 303, 307, 308):
        target = (location or "").lower()
        if any(marker in target for marker in _LOGIN_PATH_MARKERS):
            return AUTH_LOST
        return OK
    if status == 429 or status >= 500:
        return BUSY
    if status == 200:
        low = (body_excerpt or "").lower()
        if any(marker in low for marker in _AUTH_MARKERS):
            return AUTH_LOST
        return OK
    return BUSY


class SessionGuard:
    """Periodic probe with conservative relogin and capped exponential backoff."""

    def __init__(self, probe, relogin, interval=60.0, max_backoff=600.0,
                 on_event=None):
        self.probe = probe
        self.relogin = relogin
        self.interval = max(1.0, float(interval))
        self.max_backoff = max(self.interval, float(max_backoff))
        self.on_event = on_event or (lambda kind, message: None)
        self._stop = threading.Event()
        self._thread = None
        self._backoff = self.interval
        self.stats = {"checks": 0, "auth_lost": 0, "relogin_ok": 0,
                      "relogin_fail": 0, "network": 0, "busy": 0}

    def check_once(self):
        self.stats["checks"] += 1
        try:
            status, body, location = self.probe()
        except Exception as exc:                      # transport failures only
            self.stats["network"] += 1
            self.on_event(NETWORK, "探活异常：%s" % (exc,))
            return NETWORK

        kind = classify_http(status, body, location)
        if kind == OK:
            return OK
        if kind == BUSY:
            self.stats["busy"] += 1
            self.on_event(BUSY, "服务端繁忙／限流：%s" % status)
            return BUSY

        self.stats["auth_lost"] += 1
        self.on_event(AUTH_LOST, "检测到下线（%s），尝试重新登录" % status)
        try:
            relogged = bool(self.relogin())
        except Exception as exc:
            relogged = False
            self.on_event("relogin_fail", "重登抛异常：%s" % (exc,))
        if relogged:
            self.stats["relogin_ok"] += 1
            self.on_event("relogin_ok", "已重新登录")
        else:
            self.stats["relogin_fail"] += 1
            self.on_event("relogin_fail", "重登失败，等待下一轮")
        return AUTH_LOST

    def _loop(self):
        while not self._stop.is_set():
            kind = self.check_once()
            if kind in (NETWORK, BUSY) or self.stats["relogin_fail"]:
                self._backoff = min(self._backoff * 2.0, self.max_backoff)
            else:
                self._backoff = self.interval
            self._stop.wait(self._backoff)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="kingo-session-guard", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        return self

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def stats_text(self):
        s = self.stats
        return ("检查 %d · 下线 %d · 重登成功 %d · 重登失败 %d · 网络 %d · 限流 %d"
                % (s["checks"], s["auth_lost"], s["relogin_ok"],
                   s["relogin_fail"], s["network"], s["busy"]))


def probe_url_headers(url, timeout=5.0, opener=None):
    """Read-only HEAD probe: returns (status, body_excerpt, location).

    Raises on transport errors so SessionGuard can classify them as NETWORK
    instead of mistaking a timeout for a lost session.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": "KingoAssistant-session/1.0",
        "Cache-Control": "no-cache",
    })
    open_func = opener or urllib.request.urlopen
    try:
        with open_func(request, timeout=timeout) as response:
            return response.status, "", response.headers.get("Location") or ""
    except urllib.error.HTTPError as exc:
        location = ""
        if exc.headers is not None:
            location = exc.headers.get("Location") or ""
        return exc.code, "", location
